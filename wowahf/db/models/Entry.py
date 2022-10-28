from sqlalchemy import Column, Integer, DateTime, VARCHAR
from wowahf.db.db import Base


class Entry(Base):
    __tablename__ = 'entries'
    __table_args__ = {
        'mysql_row_format': 'COMPRESSED'
    }
    id = Column(Integer, nullable=False, autoincrement=True, primary_key=True)
    entry_id = Column(Integer, nullable=False, primary_key=True)
    item_id = Column(Integer, primary_key=True)
    bid = Column(Integer, nullable=False)
    buyout = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    time_left = Column(VARCHAR(16), nullable=False)
    run_uuid = Column(VARCHAR(64), nullable=False)
