from sqlalchemy import Column, Integer, ForeignKey, DateTime, Boolean, VARCHAR
from wowahf.db.db import Base


class Item(Base):
    __tablename__ = 'items'

    id = Column(Integer, nullable=False, autoincrement=True, primary_key=True)
    item_id = Column(Integer, nullable=False, unique=True)
    name = Column(VARCHAR(128), nullable=False)
    quality = Column(VARCHAR(32))
    item_class = Column(VARCHAR(64))
    item_subclass = Column(VARCHAR(64))
    vendor_buy = Column(Integer)
    vendor_sell = Column(Integer)
    level = Column(Integer)
