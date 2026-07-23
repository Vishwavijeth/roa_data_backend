SALE_UPSERT_SQL = """
INSERT INTO sale (
    transaction_type,
    saleGuid,
    listingGuid,
    agentGuid,
    createdByGuid,
    mlsNumber,
    Email,
    statusId,
    status,
    officeGuid,
    checklistTypeId,
    escrowNumber,
    escrowClosingDate,
    actualClosingDate,
    contractAcceptanceDate,
    createdOn,
    checklistModifiedOn,
    deadDate,
    reviewerGuid,
    sourceId,
    source,
    otherSource,
    dealType,
    saleTypeId,
    listingPrice,
    salePrice,
    isOfficeLead,
    coBrokerCompany,
    realPropertyType,
    realPropertySubtype,
    commercialLease,
    stageId,
    customFields,
    fileid,
    url
) VALUES %s
ON CONFLICT (saleGuid) DO UPDATE SET
    transaction_type = EXCLUDED.transaction_type,
    listingGuid = EXCLUDED.listingGuid,
    agentGuid = EXCLUDED.agentGuid,
    createdByGuid = EXCLUDED.createdByGuid,
    mlsNumber = EXCLUDED.mlsNumber,
    Email = EXCLUDED.Email,
    statusId = EXCLUDED.statusId,
    status = EXCLUDED.status,
    officeGuid = EXCLUDED.officeGuid,
    checklistTypeId = EXCLUDED.checklistTypeId,
    escrowNumber = EXCLUDED.escrowNumber,
    escrowClosingDate = EXCLUDED.escrowClosingDate,
    actualClosingDate = EXCLUDED.actualClosingDate,
    contractAcceptanceDate = EXCLUDED.contractAcceptanceDate,
    createdOn = EXCLUDED.createdOn,
    checklistModifiedOn = EXCLUDED.checklistModifiedOn,
    deadDate = EXCLUDED.deadDate,
    reviewerGuid = EXCLUDED.reviewerGuid,
    sourceId = EXCLUDED.sourceId,
    source = EXCLUDED.source,
    otherSource = EXCLUDED.otherSource,
    dealType = EXCLUDED.dealType,
    saleTypeId = EXCLUDED.saleTypeId,
    listingPrice = EXCLUDED.listingPrice,
    salePrice = EXCLUDED.salePrice,
    isOfficeLead = EXCLUDED.isOfficeLead,
    coBrokerCompany = EXCLUDED.coBrokerCompany,
    realPropertyType = EXCLUDED.realPropertyType,
    realPropertySubtype = EXCLUDED.realPropertySubtype,
    commercialLease = EXCLUDED.commercialLease,
    stageId = EXCLUDED.stageId,
    customFields = EXCLUDED.customFields,
    fileid = EXCLUDED.fileid,
    url = EXCLUDED.url
"""

FILE_CREATOR_UPSERT_SQL = """
INSERT INTO sale_file_creator (
    saleguid, guid, firstname, lastname, email, alternateemail
) VALUES %s
ON CONFLICT (saleguid, guid) DO UPDATE SET
    firstname = EXCLUDED.firstname,
    lastname = EXCLUDED.lastname,
    email = EXCLUDED.email,
    alternateemail = EXCLUDED.alternateemail
"""

PROPERTY_UPSERT_SQL = """
INSERT INTO sale_property (
    saleGuid, streetNumber, streetAddress, unit, direction,
    city, county, state, zip, yearBuilt,
    realPropertyTypeId, realPropertySubtypeId
) VALUES %s
ON CONFLICT (saleGuid) DO UPDATE SET
    streetNumber = EXCLUDED.streetNumber,
    streetAddress = EXCLUDED.streetAddress,
    unit = EXCLUDED.unit,
    direction = EXCLUDED.direction,
    city = EXCLUDED.city,
    county = EXCLUDED.county,
    state = EXCLUDED.state,
    zip = EXCLUDED.zip,
    yearBuilt = EXCLUDED.yearBuilt,
    realPropertyTypeId = EXCLUDED.realPropertyTypeId,
    realPropertySubtypeId = EXCLUDED.realPropertySubtypeId
"""

COMMISSION_UPSERT_SQL = """
INSERT INTO sale_commission (
    saleGuid, transactionCoordinatorName, transactionCoordinatorFee,
    adminBrokerageComp, dateOfCheck, datePostedToLogBook,
    listingCommissionPercent, listingCommissionAmount,
    saleCommissionPercent, saleCommissionAmount,
    otherDeductions, personalDeal, commissionBreakdownDetails,
    officeGrossCommissionOnSale
) VALUES %s
ON CONFLICT (saleGuid) DO UPDATE SET
    transactionCoordinatorName = EXCLUDED.transactionCoordinatorName,
    transactionCoordinatorFee = EXCLUDED.transactionCoordinatorFee,
    adminBrokerageComp = EXCLUDED.adminBrokerageComp,
    dateOfCheck = EXCLUDED.dateOfCheck,
    datePostedToLogBook = EXCLUDED.datePostedToLogBook,
    listingCommissionPercent = EXCLUDED.listingCommissionPercent,
    listingCommissionAmount = EXCLUDED.listingCommissionAmount,
    saleCommissionPercent = EXCLUDED.saleCommissionPercent,
    saleCommissionAmount = EXCLUDED.saleCommissionAmount,
    otherDeductions = EXCLUDED.otherDeductions,
    personalDeal = EXCLUDED.personalDeal,
    commissionBreakdownDetails = EXCLUDED.commissionBreakdownDetails,
    officeGrossCommissionOnSale = EXCLUDED.officeGrossCommissionOnSale
"""

CONTACT_UPSERT_SQL = """
INSERT INTO sale_contact (
    saleGuid, contactGuid, role, firstName, lastName,
    phoneNumber, email, company, alternatePhone,
    streetNumber, streetName, zip, city, state,
    fax, notes, isTrustCompanyOrOtherEntity, isCashDeal,
    loanTypeId, loanType, loanAmount, brokerTaxId, miscContactType
) VALUES %s
ON CONFLICT (saleGuid, contactGuid, role) DO UPDATE SET
    firstName = EXCLUDED.firstName,
    lastName = EXCLUDED.lastName,
    phoneNumber = EXCLUDED.phoneNumber,
    email = EXCLUDED.email,
    company = EXCLUDED.company,
    alternatePhone = EXCLUDED.alternatePhone,
    streetNumber = EXCLUDED.streetNumber,
    streetName = EXCLUDED.streetName,
    zip = EXCLUDED.zip,
    city = EXCLUDED.city,
    state = EXCLUDED.state,
    fax = EXCLUDED.fax,
    notes = EXCLUDED.notes,
    isTrustCompanyOrOtherEntity = EXCLUDED.isTrustCompanyOrOtherEntity,
    isCashDeal = EXCLUDED.isCashDeal,
    loanTypeId = EXCLUDED.loanTypeId,
    loanType = EXCLUDED.loanType,
    loanAmount = EXCLUDED.loanAmount,
    brokerTaxId = EXCLUDED.brokerTaxId,
    miscContactType = EXCLUDED.miscContactType
"""

CO_AGENT_UPSERT_SQL = """
INSERT INTO sale_co_agent (saleGuid, coAgentGuid) VALUES %s
ON CONFLICT (saleGuid, coAgentGuid) DO NOTHING
"""

COORDINATOR_UPSERT_SQL = """
INSERT INTO sale_transaction_coordinator (
    saleGuid, contactGuid, firstName, lastName, fullName,
    email, phoneNumber, notes, fee, hasAccess
) VALUES %s
ON CONFLICT (saleGuid, contactGuid) DO UPDATE SET
    firstName = EXCLUDED.firstName,
    lastName = EXCLUDED.lastName,
    fullName = EXCLUDED.fullName,
    email = EXCLUDED.email,
    phoneNumber = EXCLUDED.phoneNumber,
    notes = EXCLUDED.notes,
    fee = EXCLUDED.fee,
    hasAccess = EXCLUDED.hasAccess
"""

SPLIT_UPSERT_SQL = """
INSERT INTO sale_commission_split (saleGuid, agentGuid, amount, percentage)
VALUES %s
ON CONFLICT (saleGuid, agentGuid) DO UPDATE SET
    amount = EXCLUDED.amount,
    percentage = EXCLUDED.percentage
"""

REFERRAL_UPSERT_SQL = """
INSERT INTO sale_commission_referral (
    saleGuid, typeId, typeName, contactGuid,
    contactFirstName, contactLastName, contactEmail, contactPhoneNumber,
    brokerageName, amount
) VALUES %s
ON CONFLICT (saleGuid) DO UPDATE SET
    typeId = EXCLUDED.typeId,
    typeName = EXCLUDED.typeName,
    contactGuid = EXCLUDED.contactGuid,
    contactFirstName = EXCLUDED.contactFirstName,
    contactLastName = EXCLUDED.contactLastName,
    contactEmail = EXCLUDED.contactEmail,
    contactPhoneNumber = EXCLUDED.contactPhoneNumber,
    brokerageName = EXCLUDED.brokerageName,
    amount = EXCLUDED.amount
"""

EMD_UPSERT_SQL = """
INSERT INTO sale_earnest_money_deposit (
    saleGuid, isEarnestMoneyHeld, depositAmount, depositDueDate,
    datePostedToLogBook, dateOfCheck, additionalDepositAmount, additionalDepositDueDate
) VALUES %s
ON CONFLICT (saleGuid) DO UPDATE SET
    isEarnestMoneyHeld = EXCLUDED.isEarnestMoneyHeld,
    depositAmount = EXCLUDED.depositAmount,
    depositDueDate = EXCLUDED.depositDueDate,
    datePostedToLogBook = EXCLUDED.datePostedToLogBook,
    dateOfCheck = EXCLUDED.dateOfCheck,
    additionalDepositAmount = EXCLUDED.additionalDepositAmount,
    additionalDepositDueDate = EXCLUDED.additionalDepositDueDate
"""

ACTIVITY_UPSERT_SQL = """
INSERT INTO sale_checklist_activity (
    saleGuid, activityId, "order", activityName, dateAssigned,
    typeId, typeName, status, help, modifiedOn
) VALUES %s
ON CONFLICT (saleGuid, activityId) DO UPDATE SET
    "order" = EXCLUDED."order",
    activityName = EXCLUDED.activityName,
    dateAssigned = EXCLUDED.dateAssigned,
    typeId = EXCLUDED.typeId,
    typeName = EXCLUDED.typeName,
    status = EXCLUDED.status,
    help = EXCLUDED.help,
    modifiedOn = EXCLUDED.modifiedOn
"""

DOC_UPSERT_SQL = """
INSERT INTO sale_checklist_doc (
    saleGuid, activityId, docId, name, url,
    documentServiceKey, modifiedDate, uploadDate, fileName,
    extension, fileSize, pages
) VALUES %s
ON CONFLICT (docId, saleGuid) DO UPDATE SET
    activityId = EXCLUDED.activityId,
    name = EXCLUDED.name,
    url = EXCLUDED.url,
    documentServiceKey = EXCLUDED.documentServiceKey,
    modifiedDate = EXCLUDED.modifiedDate,
    uploadDate = EXCLUDED.uploadDate,
    fileName = EXCLUDED.fileName,
    extension = EXCLUDED.extension,
    fileSize = EXCLUDED.fileSize,
    pages = EXCLUDED.pages
"""

ACTIVITY_DOC_UPSERT_SQL = """
INSERT INTO sale_checklist_activity_docs (saleGuid, activityId, fileName)
VALUES %s
ON CONFLICT (saleGuid, activityId, fileName) DO NOTHING
"""

BREAKDOWN_INSERT_SQL = """
INSERT INTO sale_commission_breakdown (saleGuid, name, details, amount)
VALUES %s
"""

COMMENT_INSERT_SQL = """
INSERT INTO sale_checklist_comment (activityId, saleGuid, comment, createdOn, createdBy)
VALUES %s
"""