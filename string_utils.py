def TruncateString(s, max_length=50, suffix='...'):
    if not s or len(s) <= max_length:
        return s
    return s[:max_length - len(suffix)] + suffix


def PascalCaseToDictKey(pascal_key):
    from gvsdsdk.model_base import pascal_to_snake
    return pascal_to_snake(pascal_key)
