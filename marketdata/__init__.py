__all__ = ["MarketDataset"]


def __getattr__(name):
    if name == "MarketDataset":
        from .reader import MarketDataset
        return MarketDataset
    raise AttributeError(name)
