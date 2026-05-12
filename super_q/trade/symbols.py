"""掘金量化股票代码转换。"""


def to_gm_symbol(symbol: str) -> str:
    """将 6 位 A 股代码转换为掘金格式。"""
    code = symbol.strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"非法股票代码：{symbol}")

    if code.startswith(("4", "8")):
        exchange = "BJSE"
    elif code.startswith(("6", "9")):
        exchange = "SHSE"
    else:
        exchange = "SZSE"
    return f"{exchange}.{code}"
