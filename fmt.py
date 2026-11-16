def inr(x, decimals: int = 0) -> str:
    """₹ with Indian digit grouping (1,14,500)."""
    if x is None:
        return "—"
    neg = x < 0
    x = abs(float(x))
    whole, _, frac = f"{x:.{max(decimals, 0)}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return ("-" if neg else "") + "₹" + whole + (("." + frac) if decimals else "")


def pct(x: float, signed: bool = True) -> str:
    return f"{x:+.1f}%" if signed else f"{x:.1f}%"
