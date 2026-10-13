"""Merchant catalog with messy bank descriptors for realistic matching."""

from __future__ import annotations

MERCHANTS = [
    # subscriptions / streaming
    {"merchant_id": "m_netflix", "name": "Netflix", "category": "Subscriptions", "typical_amount": 15.49, "is_subscription": True, "descriptors": ["NETFLIX.COM", "NETFLIX *STREAMING", "NFLX DIGITAL"]},
    {"merchant_id": "m_spotify", "name": "Spotify", "category": "Subscriptions", "typical_amount": 10.99, "is_subscription": True, "descriptors": ["SPOTIFY USA", "SPOTIFY*P123", "SPOTIFY AB"]},
    {"merchant_id": "m_apple_music", "name": "Apple Music", "category": "Subscriptions", "typical_amount": 10.99, "is_subscription": True, "descriptors": ["APPLE.COM/BILL", "APPLE MUSIC", "APL*APPLE MUSIC"]},
    {"merchant_id": "m_hulu", "name": "Hulu", "category": "Subscriptions", "typical_amount": 17.99, "is_subscription": True, "descriptors": ["HULU LLC", "HULU*STREAMING"]},
    {"merchant_id": "m_disney", "name": "Disney+", "category": "Subscriptions", "typical_amount": 13.99, "is_subscription": True, "descriptors": ["DISNEYPLUS", "DISNEY+ *SUB"]},
    {"merchant_id": "m_icloud", "name": "iCloud+", "category": "Subscriptions", "typical_amount": 2.99, "is_subscription": True, "descriptors": ["APPLE.COM/BILL ICLOUD", "ICLOUD+ STORAGE"]},
    {"merchant_id": "m_dropbox", "name": "Dropbox", "category": "Subscriptions", "typical_amount": 11.99, "is_subscription": True, "descriptors": ["DROPBOX*PLUS", "DROPBOX.COM"]},
    {"merchant_id": "m_gym", "name": "FitLife Gym", "category": "Health", "typical_amount": 39.99, "is_subscription": True, "descriptors": ["FITLIFE GYM #042", "FITLIFE MEMBERSHIP"]},
    {"merchant_id": "m_aws", "name": "AWS", "category": "Subscriptions", "typical_amount": 18.50, "is_subscription": True, "descriptors": ["Amazon web services", "AWS.AMAZON.COM"]},
    # groceries / food
    {"merchant_id": "m_wholefoods", "name": "Whole Foods", "category": "Groceries", "typical_amount": 65.0, "is_subscription": False, "descriptors": ["WHOLEFDS MKT 102", "WF *WHOLE FOODS"]},
    {"merchant_id": "m_traderjoe", "name": "Trader Joe's", "category": "Groceries", "typical_amount": 48.0, "is_subscription": False, "descriptors": ["TRADER JOE'S #118", "TRADER JOE S"]},
    {"merchant_id": "m_costco", "name": "Costco", "category": "Groceries", "typical_amount": 140.0, "is_subscription": False, "descriptors": ["COSTCO WHSE #0451", "COSTCO GAS"]},
    {"merchant_id": "m_starbucks", "name": "Starbucks", "category": "Dining", "typical_amount": 6.5, "is_subscription": False, "descriptors": ["STARBUCKS STORE 8821", "SBUX*STORE"]},
    {"merchant_id": "m_chipotle", "name": "Chipotle", "category": "Dining", "typical_amount": 14.0, "is_subscription": False, "descriptors": ["CHIPOTLE 2041", "CHIPOTLE ONLINE"]},
    {"merchant_id": "m_doordash", "name": "DoorDash", "category": "Dining", "typical_amount": 28.0, "is_subscription": False, "descriptors": ["DOORDASH*REST", "DD *DOORDASH"]},
    {"merchant_id": "m_uber_eats", "name": "Uber Eats", "category": "Dining", "typical_amount": 26.0, "is_subscription": False, "descriptors": ["UBER EATS", "UBER *EATS"]},
    # transport / retail / bills
    {"merchant_id": "m_uber", "name": "Uber", "category": "Transport", "typical_amount": 18.0, "is_subscription": False, "descriptors": ["UBER *TRIP", "UBER TECHNOLOGIES"]},
    {"merchant_id": "m_shell", "name": "Shell", "category": "Transport", "typical_amount": 45.0, "is_subscription": False, "descriptors": ["SHELL OIL 574829", "SHELL #12093"]},
    {"merchant_id": "m_amazon", "name": "Amazon", "category": "Shopping", "typical_amount": 42.0, "is_subscription": False, "descriptors": ["AMZN Mktp US*1A2B", "AMAZON.COM*AB12", "AMZN Digital"]},
    {"merchant_id": "m_target", "name": "Target", "category": "Shopping", "typical_amount": 55.0, "is_subscription": False, "descriptors": ["TARGET 00012345", "TARGET.COM"]},
    {"merchant_id": "m_rent", "name": "Harbor Property Mgmt", "category": "Housing", "typical_amount": 1850.0, "is_subscription": True, "descriptors": ["HARBOR PM RENT", "ACH RENT HARBOR"]},
    {"merchant_id": "m_electric", "name": "City Power", "category": "Utilities", "typical_amount": 95.0, "is_subscription": True, "descriptors": ["CITY POWER ELEC", "UTIL*CITYPOWER"]},
    {"merchant_id": "m_internet", "name": "Comcast", "category": "Utilities", "typical_amount": 79.99, "is_subscription": True, "descriptors": ["COMCAST CABLE", "XFINITY*BILL"]},
    {"merchant_id": "m_salary", "name": "Employer Payroll", "category": "Income", "typical_amount": 4200.0, "is_subscription": False, "descriptors": ["ACME CORP PAYROLL", "DIR DEP ACME"]},
    {"merchant_id": "m_transfer", "name": "Venmo", "category": "Transfers", "typical_amount": 40.0, "is_subscription": False, "descriptors": ["VENMO *PAYMENT", "VENMO CASHOUT"]},
    {"merchant_id": "m_pharmacy", "name": "CVS", "category": "Health", "typical_amount": 22.0, "is_subscription": False, "descriptors": ["CVS/PHARMACY #441", "CVS CAREMARK"]},
    {"merchant_id": "m_airline", "name": "United Airlines", "category": "Travel", "typical_amount": 320.0, "is_subscription": False, "descriptors": ["UNITED 016238", "UAL*TICKET"]},
    {"merchant_id": "m_hotel", "name": "Marriott", "category": "Travel", "typical_amount": 210.0, "is_subscription": False, "descriptors": ["MARRIOTT HOTELS", "MARRIOTT*STAY"]},
    {"merchant_id": "m_unknown_fraud", "name": "Unknown Merchant", "category": "Other", "typical_amount": 1.0, "is_subscription": False, "descriptors": ["POS 99281 UNKNOWN", "WEB*GLOBALSOFT LTD", "AUTH HOLD *TEST"]},
]

CATEGORY_BUDGET_SHARES = {
    "Housing": 0.32,
    "Groceries": 0.12,
    "Dining": 0.10,
    "Transport": 0.08,
    "Utilities": 0.07,
    "Subscriptions": 0.05,
    "Shopping": 0.10,
    "Health": 0.04,
    "Travel": 0.04,
    "Transfers": 0.03,
    "Other": 0.05,
}