from sqlalchemy import Column, Integer, ForeignKey, DateTime, Boolean, VARCHAR
from wowahf.db.db import Base


class Run(Base):
    __tablename__ = 'parser_runs'

    id = Column(Integer, nullable=False, autoincrement=True, primary_key=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime)
    uuid = Column(VARCHAR(64), nullable=False)
    errors = Column(Integer)
    entries_processed = Column(Integer)
    items_added = Column(Integer)
    wowahf_version = Column(VARCHAR(8), nullable=False)
