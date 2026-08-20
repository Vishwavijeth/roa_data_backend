from sqlalchemy import Date, Integer, Numeric, String, ForeignKeyConstraint, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class SaleChecklistActivity(Base):
    __tablename__ = "sale_checklist_activity"
    saleGuid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    activityId = Column(String, primary_key=True, nullable=False)
    order = Column("order", Integer, nullable=True)
    activityName = Column(String, nullable=True)
    dateAssigned = Column(Date, nullable=True)
    typeId = Column(Integer, nullable=True)
    typeName = Column(String, nullable=True)
    status = Column(String, nullable=True)
    help = Column(String, nullable=True)
    modifiedOn = Column(Date, nullable=True)


class SaleChecklistDoc(Base):
    __tablename__ = "sale_checklist_doc"
    docId = Column(String, primary_key=True, nullable=False)
    saleGuid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    activityId = Column(String, nullable=True)
    name = Column(String, nullable=True)
    url = Column(String, nullable=True)
    documentServiceKey = Column(String, nullable=True)
    modifiedDate = Column(Date, nullable=True)
    uploadDate = Column(Date, nullable=True)
    fileName = Column(String, nullable=True)
    extension = Column(String, nullable=True)
    fileSize = Column(Numeric, nullable=True)
    pages = Column(Integer, nullable=True)
    __table_args__ = (ForeignKeyConstraint(["activityId", "saleGuid"], ["sale_checklist_activity.activityId", "sale_checklist_activity.saleGuid"], ondelete="CASCADE"),)


class SaleChecklistActivityDocs(Base):
    __tablename__ = "sale_checklist_activity_docs"
    saleGuid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    activityId = Column(String, primary_key=True, nullable=False)
    fileName = Column(String, primary_key=True, nullable=False)
    __table_args__ = (ForeignKeyConstraint(["activityId", "saleGuid"], ["sale_checklist_activity.activityId", "sale_checklist_activity.saleGuid"], ondelete="CASCADE"),)


class SaleChecklistComment(Base):
    __tablename__ = "sale_checklist_comment"
    id = Column(Integer, primary_key=True, autoincrement=True)
    activityId = Column(String, nullable=True)
    saleGuid = Column(UUID(as_uuid=True), nullable=False)
    comment = Column(String, nullable=True)
    createdOn = Column(Date, nullable=True)
    createdBy = Column(String, nullable=True)
    __table_args__ = (ForeignKeyConstraint(["activityId", "saleGuid"], ["sale_checklist_activity.activityId", "sale_checklist_activity.saleGuid"], ondelete="CASCADE"),)