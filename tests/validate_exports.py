"""Validate the actual browser-generated history workbook without Excel dependencies."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
with zipfile.ZipFile(Path(sys.argv[1])) as archive:
    assert archive.testzip() is None
    for name in archive.namelist():
        if name.endswith((".xml", ".rels")):
            ET.fromstring(archive.read(name))
    sheets = []
    for i in range(1, 4):
        xml = ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
        rows = [["".join(c.itertext()) for c in r.findall("s:c", ns)] for r in xml.findall(".//s:row", ns)]
        sheets.append(rows)
    # Current root + 2 historical plans; root synthesis attempts are included.
    assert [len(rows) for rows in sheets] == [7, 4, 7]
    assert len({row[-4] for row in sheets[0][1:]}) == 2
    assert len({row[-3] for row in sheets[0][1:]}) == 3
    attempt_columns = {name: index for index, name in enumerate(sheets[2][0])}
    assert [r[attempt_columns["尝试次数"]] for r in sheets[2][1:] if r[attempt_columns["任务 ID"]] == "c"] == ["1", "2"]
    assert any("处理 <script>文本 & 中文 😀" in r for r in sheets[0])
    assert all(r[attempt_columns["模型"]] == "audit-fixture" for r in sheets[2][1:])
print(json.dumps({"zip_crc": "pass", "xml": "pass", "sheet_rows": [len(r) for r in sheets], "task_instances": 2, "rounds": 3, "unicode": "pass", "synthesis_attempts": "pass"}))
