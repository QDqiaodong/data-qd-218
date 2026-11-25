# data-qd-218 · 钻芯回收、定位修订与化验交付

核对钻进回次、实体岩芯长度和深度岩性区间，计算每回次精确回收率；同时提供一个纯 Python 标准库、可离线重开的钻孔档案，用于：

- 保存每回次从浅到深的岩芯块顺序；
- 用整批新证据替换式求解块端深度的可行范围；
- 精确判定样本是 `located`、`unresolved` 还是回次 `conflict`；
- 收存化验回执，但只向地质模型交付能唯一归属编录区间的记录；
- 生成按 assay ID 排序、“先撤回、再新增”的修订交接记录；
- 支持离线重试、原序号/原交接记录重放、历史定位结论查询和崩溃原子提交。

Python 3.11+；存储使用标准库 `sqlite3`，求解使用 `fractions.Fraction`，没有第三方运行时依赖。

## 保留的 `process(data)` 接口

原有输入、输出和错误语义保持不变：

```bash
PYTHONPATH=src python examples/run_sample.py
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 新档案 API 示例

可直接运行完整示例：

```bash
python3 examples/revision_workflow.py
```

最小示例：

```python
from core_recovery import (
    create_archive, open_archive,
    sample, point_evidence, distance_evidence, assay_receipt,
)

base = {
    "runs": [{"id": "r1", "from_m": "0", "to_m": "10"}],
    "pieces": [
        {"id": "p", "run_id": "r1", "length_m": "2"},
        {"id": "q", "run_id": "r1", "length_m": "2"},
    ],
    "lithology": [
        {"id": "L", "from_m": "0", "to_m": "4", "code": "same"},
        {"id": "M", "from_m": "4", "to_m": "10", "code": "same"},
    ],
}

archive = create_archive(
    "/path/to/hole.core",
    base,
    piece_order={"r1": ["p", "q"]},  # 无块回次用 []
    samples=[sample("A", "p", "0", "1")],
)

archive.submit_revision(
    "field-rev-1",
    evidence=[
        point_evidence("q", "0", "5", "6"),
        distance_evidence("p", "0", "q", "0", "3", "3"),
    ],
    receipts=[assay_receipt("a-1", "A", "Cu", "pct", "0.420")],
)

print(archive.available_assays())
print(archive.sample_location("A", seq=1))
print(archive.handoff(seq=1))
archive.close()

archive = open_archive("/path/to/hole.core")
print(archive.locations())
```

若 `path` 是已存在的目录，档案文件为该目录下的 `core_archive.sqlite3`；否则把 `path` 当作 SQLite 档案文件路径。

## 输入约定

所有深度、长度、偏移、界限和化验值均使用有限十进制字符串，例如 `"2"`、`"2.00"`、`"-0.25"`；不要传 `float`。等价拼写（`"1.0"` 与 `"1.00"`）在幂等比较时视为相同，并规范化为稳定的无指数十进制输出。

### 定位证据

- 块上刻度点的绝对深度闭区间：

  ```python
  point_evidence(piece_id="q", offset_m="0",
                 low_depth_m="5", high_depth_m="6")
  ```

  刻度偏移范围为 `[0, piece_length]`，两端均可达。

- 同回次两块刻度的深度差闭区间。两个块参数可按任意顺序传入，内部按建档的浅深顺序规范化：

  ```python
  distance_evidence("p", "0", "q", "0", "3", "3")
  ```

每次修订的 `evidence` 是一整套替换上一版的新证据。完全相同的证据按集合去重；证据排列不影响载荷。

### 样本与化验回执

样本偏移是块内半开区间 `[from_offset_m, to_offset_m)`，必须非空且不能超出块长。

回执字段为唯一非空 `assay_id`、已登记 `sample_id`、非空 `analyte`、非空 `unit` 和有限十进制 `value`。同一样本可有多条回执；同一修订批次内不得重复 assay ID，新修订也不能重用档案中已有的 assay ID。

## 定位结论

`archive.sample_location(sample_id, seq=None)` 返回：

- `status="conflict"`：该回次的顺序、长度、回次边界和全部证据联合无解；包括超回收；
- `status="unresolved"`：回次有合法摆放，但没有一个编录区间能在所有合法摆放下完整容纳该样本；
- `status="located"`：所有合法摆放都把完整样本归到同一个编录区间，结果含 `lithology_id` 和 `code`。

有解时还返回两个均可达到的界：

- `shallowest_from_m`：所有合法摆放中最浅的样本起点；
- `deepest_to_m`：所有合法摆放中最深的样本终点。

岩性区间按半开处理，因此样本终点恰好等于岩性区间终点仍算包含。相邻但 ID 不同的同 code 区间不会合并。

## 交付、撤回与离线重试

`archive.available_assays()` 返回当前仅含 `located` 样本的化验记录，按 `assay_id` 排序；记录附 `run_id`、`piece_id`、`sample_id`、`lithology_id`、`code` 以及化验字段。

每次成功的新修订都获得从 1 开始、连续无跳号的 `seq`，即使交接列表为空。`archive.handoff(seq)` 返回该版本的原始交接：

```json
{
  "seq": 2,
  "revision_id": "field-rev-2",
  "withdrawals": [{"assay_id": "a-1", "lithology_id": "L", "...": "..."}],
  "additions": [{"assay_id": "a-1", "lithology_id": "M", "...": "..."}]
}
```

应用顺序固定为先 `withdrawals`、后 `additions`；两个列表分别按 assay ID 稳定排序。仅定位包络变宽/变窄但编录归属不变时，不产生交接记录。改归另一个编录 ID 时，旧记录进入撤回、新记录进入新增，即使两个 `code` 相同。

同一 `revision_id` 加同一业务载荷的重试返回原 `seq` 和原交接记录，不会按最新定位重算，也不会因原批次 assay ID 已入库而失败。同一 `revision_id` 携带不同载荷会抛出 `RevisionPayloadConflict`。结构校验先于重试比较。

证据、回执、序号、当前交付和不可变交接记录在同一个 SQLite 事务中提交。进程在提交前中断后重开，只会看到上一个完整版本；失败或被拒绝的批次不会消耗序号或修订 ID。

也可用模块级函数在调用后自动关闭档案：

```python
from core_recovery import (
    submit_revision, read_available_assays,
    read_locations, read_sample_location, read_handoff,
)
```

本仓不解释地质成因、不猜测缺失段位置，也不管理仓库。
