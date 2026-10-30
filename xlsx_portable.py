"""Produce valid XLSX files using stdlib XML/ZIP only (no spreadsheet dependency)."""
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring, register_namespace
from zipfile import ZIP_DEFLATED, ZipFile

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"

def tag(ns, name):
    return "{"+ns+"}"+name

def xml(root):
    # Some readers reject prefixed roots; use the matching default namespace.
    register_namespace("",root.tag.split("}")[0][1:])
    register_namespace("r",REL)
    return (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'+
            tostring(root,encoding="utf-8"))

def col(index):
    result = ""
    index += 1
    while index:
        index, rem = divmod(index-1,26)
        result = chr(65+rem)+result
    return result

def safe(value):
    value = "" if value is None else str(value)
    value = "".join(c if ord(c) in (9,10,13) or ord(c)>=32 else "\uFFFD" for c in value)
    return value[:32767]

def worksheet(headers, values):
    root = Element(tag(MAIN,"worksheet"))
    views = SubElement(root,tag(MAIN,"sheetViews"))
    view = SubElement(views,tag(MAIN,"sheetView"),{"workbookViewId":"0"})
    SubElement(view,tag(MAIN,"pane"),{
        "ySplit":"1","topLeftCell":"A2","activePane":"bottomLeft","state":"frozen"})
    widths = SubElement(root,tag(MAIN,"cols"))
    for i, header in enumerate(headers,1):
        SubElement(widths,tag(MAIN,"col"),{
            "min":str(i),"max":str(i),"width":"42" if header in ("text","cover_letter") else "22",
            "customWidth":"1"})
    sheet_data = SubElement(root,tag(MAIN,"sheetData"))
    for row_num,row_values in enumerate([headers]+values,1):
        row = SubElement(sheet_data,tag(MAIN,"row"),{"r":str(row_num)})
        for col_num,value in enumerate(row_values):
            attrs = {"r":f"{col(col_num)}{row_num}","t":"inlineStr"}
            if row_num==1: attrs["s"]="1"
            cell = SubElement(row,tag(MAIN,"c"),attrs)
            inline = SubElement(cell,tag(MAIN,"is"))
            SubElement(inline,tag(MAIN,"t"),{tag(XML_NS,"space"):"preserve"}).text = safe(value)
    if values:
        SubElement(root,tag(MAIN,"autoFilter"),{
            "ref":f"A1:{col(len(headers)-1)}{len(values)+1}"})
    return xml(root)

def styles():
    root = Element(tag(MAIN,"styleSheet"))
    fonts = SubElement(root,tag(MAIN,"fonts"),{"count":"2"})
    regular = SubElement(fonts,tag(MAIN,"font"))
    SubElement(regular,tag(MAIN,"sz"),{"val":"11"})
    SubElement(regular,tag(MAIN,"name"),{"val":"Aptos"})
    bold = SubElement(fonts,tag(MAIN,"font"))
    SubElement(bold,tag(MAIN,"b"))
    SubElement(bold,tag(MAIN,"sz"),{"val":"11"})
    SubElement(bold,tag(MAIN,"color"),{"rgb":"FFFFFFFF"})
    fills = SubElement(root,tag(MAIN,"fills"),{"count":"3"})
    for pattern in ("none","gray125"):
        SubElement(SubElement(fills,tag(MAIN,"fill")),tag(MAIN,"patternFill"),{
            "patternType":pattern})
    solid = SubElement(SubElement(fills,tag(MAIN,"fill")),tag(MAIN,"patternFill"),{
        "patternType":"solid"})
    SubElement(solid,tag(MAIN,"fgColor"),{"rgb":"FF22324E"})
    SubElement(solid,tag(MAIN,"bgColor"),{"indexed":"64"})
    border = SubElement(SubElement(root,tag(MAIN,"borders"),{"count":"1"}),tag(MAIN,"border"))
    for side in ("left","right","top","bottom","diagonal"):
        SubElement(border,tag(MAIN,side))
    normal = SubElement(root,tag(MAIN,"cellStyleXfs"),{"count":"1"})
    SubElement(normal,tag(MAIN,"xf"),{"numFmtId":"0","fontId":"0","fillId":"0","borderId":"0"})
    xfs = SubElement(root,tag(MAIN,"cellXfs"),{"count":"2"})
    SubElement(xfs,tag(MAIN,"xf"),{"numFmtId":"0","fontId":"0","fillId":"0","borderId":"0","xfId":"0"})
    SubElement(xfs,tag(MAIN,"xf"),{"numFmtId":"0","fontId":"1","fillId":"2","borderId":"0",
                                   "xfId":"0","applyFill":"1","applyFont":"1"})
    SubElement(SubElement(root,tag(MAIN,"cellStyles"),{"count":"1"}),
               tag(MAIN,"cellStyle"),{"name":"Normal","xfId":"0","builtinId":"0"})
    return xml(root)

def write_xlsx(path, sheet_data):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    workbook = Element(tag(MAIN,"workbook"))
    sheets = SubElement(workbook,tag(MAIN,"sheets"))
    rels = Element(tag(PKG,"Relationships"))
    types = Element(tag(CT,"Types"))
    SubElement(types,tag(CT,"Default"),{"Extension":"rels",
        "ContentType":"application/vnd.openxmlformats-package.relationships+xml"})
    SubElement(types,tag(CT,"Default"),{"Extension":"xml","ContentType":"application/xml"})
    for part, mime in (
        ("/xl/workbook.xml","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
        ("/xl/styles.xml","application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"),
    ):
        SubElement(types,tag(CT,"Override"),{"PartName":part,"ContentType":mime})
    with ZipFile(path,"w",ZIP_DEFLATED) as z:
        for i,(name,(headers,rows)) in enumerate(sheet_data.items(),1):
            SubElement(sheets,tag(MAIN,"sheet"),{"name":name,"sheetId":str(i),tag(REL,"id"):f"rId{i}"})
            SubElement(rels,tag(PKG,"Relationship"),{"Id":f"rId{i}",
                "Type":REL+"/worksheet","Target":f"worksheets/sheet{i}.xml"})
            SubElement(types,tag(CT,"Override"),{
                "PartName":f"/xl/worksheets/sheet{i}.xml",
                "ContentType":"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
            z.writestr(f"xl/worksheets/sheet{i}.xml",worksheet(headers,rows))
        SubElement(rels,tag(PKG,"Relationship"),{"Id":f"rId{len(sheet_data)+1}",
            "Type":REL+"/styles","Target":"styles.xml"})
        package = Element(tag(PKG,"Relationships"))
        SubElement(package,tag(PKG,"Relationship"),{"Id":"rId1",
            "Type":REL+"/officeDocument","Target":"xl/workbook.xml"})
        z.writestr("_rels/.rels",xml(package))
        z.writestr("xl/_rels/workbook.xml.rels",xml(rels))
        z.writestr("xl/workbook.xml",xml(workbook))
        z.writestr("xl/styles.xml",styles())
        z.writestr("[Content_Types].xml",xml(types))
