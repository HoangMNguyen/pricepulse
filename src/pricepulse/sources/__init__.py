from pricepulse.sources.base import Source, SourceError
from pricepulse.sources.ikea import IkeaSource
from pricepulse.sources.uniqlo import UniqloSource

SOURCES: dict[str, Source] = {IkeaSource.code: IkeaSource(), UniqloSource.code: UniqloSource()}


def get_source(code: str) -> Source:
    try:
        return SOURCES[code]
    except KeyError:
        raise SourceError(f"unknown source {code!r}; known: {sorted(SOURCES)}") from None


__all__ = ["SOURCES", "Source", "SourceError", "get_source"]
