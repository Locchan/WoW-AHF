# Import run_parse from wowahf.pipeline.run to avoid circular imports
# with wowahf.db.writes -> normalize via this package __init__.

def __getattr__(name: str):
    if name == "run_parse":
        from wowahf.pipeline.run import run_parse

        return run_parse
    raise AttributeError(name)


__all__ = ["run_parse"]
