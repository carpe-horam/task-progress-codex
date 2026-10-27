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
    for i in range(1, 6):
        xml = ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
        rows = [["".join(c.itertext()) for c in r.findall("s:c", ns)] for r in xml.findall(".//s:row", ns)]
        sheets.append(rows)
    task_sheet = next(rows for rows in sheets if {"任务 ID", "任务名称", "完整路径"}.issubset(rows[0]))
    attempt_sheet = next(rows for rows in sheets if {"任务 ID", "尝试次数", "模型", "类型", "有效性"}.issubset(rows[0]))
    evidence_sheet = next(rows for rows in sheets if {"证据来源", "证据内容", "序号"}.issubset(rows[0]))
    # Current root + 2 historical plans; root synthesis attempts are included.
    assert [len(rows) for rows in (task_sheet, attempt_sheet, evidence_sheet)] == [7, 7, 3]
    task_columns = {name: index for index, name in enumerate(task_sheet[0])}
    assert len({row[task_columns["结果摘要"]] for row in task_sheet[1:]}) == 2
    assert any(row[task_columns["证据"]] == "https://example.invalid/audit" for row in task_sheet[1:])
    attempt_columns = {name: index for index, name in enumerate(attempt_sheet[0])}
    c_attempts = {r[attempt_columns["尝试次数"]] for r in attempt_sheet[1:] if r[attempt_columns["任务 ID"]] == "c"}
    assert {"1", "2"}.issubset(c_attempts)
    assert any(r[attempt_columns["任务 ID"]] == "c" and r[attempt_columns["尝试次数"]] == "1"
               and r[attempt_columns["派发文本"]] == "First attempt line 1\nFirst attempt line 2"
               for r in attempt_sheet[1:])
    assert any("处理 <script>文本 & 中文 😀" in r for r in task_sheet)
    assert all(r[attempt_columns["模型"]] == "audit-fixture" for r in attempt_sheet[1:])
print(json.dumps({"zip_crc": "pass", "xml": "pass", "task_rows": len(task_sheet), "attempt_rows": len(attempt_sheet), "evidence_rows": len(evidence_sheet), "task_instances": 2, "rounds": 3, "unicode": "pass", "synthesis_attempts": "pass"}))
