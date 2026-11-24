def _valid_gtin_checksum(value: str) -> bool:
    data = value[:-1]
    checksum = sum(
        int(digit) * (3 if (len(data) - index) % 2 else 1)
        for index, digit in enumerate(data)
    )
    return (10 - checksum % 10) % 10 == int(value[-1])


def _expand_upc_e(value: str) -> str | None:
    number_system, *digits, check_digit = value
    if number_system not in {"0", "1"}:
        return None

    first, second, third, fourth, fifth, last = digits
    if last in {"0", "1", "2"}:
        body = number_system + first + second + last + "0000" + third + fourth + fifth
    elif last == "3":
        body = number_system + first + second + third + "00000" + fourth + fifth
    elif last == "4":
        body = number_system + first + second + third + fourth + "00000" + fifth
    else:
        body = number_system + first + second + third + fourth + fifth + "0000" + last
    return body + check_digit


def is_valid_gtin(value: str) -> bool:
    if not value.isascii() or not value.isdigit():
        return False
    if len(value) in {12, 13, 14}:
        return _valid_gtin_checksum(value)
    if len(value) == 8:
        return _valid_gtin_checksum(value) or (
            (expanded := _expand_upc_e(value)) is not None
            and _valid_gtin_checksum(expanded)
        )
    return False
