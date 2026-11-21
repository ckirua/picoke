"""YAML document file I/O."""

from os import PathLike


def read_yaml(path: str | PathLike[str]) -> object:
    """Read one YAML document from a strict UTF-8 text file."""
    try:
        import yaml
    except ModuleNotFoundError as exc:
        if exc.name != 'yaml':
            raise
        raise ImportError('read_yaml requires picoke[yaml]') from exc

    with open(path, 'r', encoding='utf-8', errors='strict', newline='') as stream:
        return yaml.safe_load(stream)


def write_yaml(path: str | PathLike[str], value: object) -> None:
    """Serialize one YAML document before overwriting its destination."""
    try:
        import yaml
    except ModuleNotFoundError as exc:
        if exc.name != 'yaml':
            raise
        raise ImportError('write_yaml requires picoke[yaml]') from exc

    document = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    with open(path, 'w', encoding='utf-8', newline='') as stream:
        stream.write(document)
