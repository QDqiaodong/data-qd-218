# data-qd-218 · 钻芯回收与岩性区间核算

核对钻进回次、实体岩芯长度和深度岩性区间，计算每回次精确回收率，并保留超回收与跨回次区间错误。

Python 3.11+；运行只用标准库，构建固定 setuptools 80.9.0 与 wheel 0.45.1。

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python --offline -r requirements-build.lock
    uv pip install --python .venv/bin/python --offline --no-build-isolation -e .
    .venv/bin/python -m unittest discover -s tests -v
    .venv/bin/python examples/run_sample.py

样例为原创合成钻孔数据。本仓不解释地质成因、不管理库存，也不自动猜测岩芯盒内深度位置。

