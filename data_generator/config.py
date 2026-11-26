"""Global configuration for the Coforce synthetic data generator."""
from datetime import date

COMPANY = "Coforce"
EMAIL_DOMAIN = "coforce.com"
TODAY = date(2026, 11, 25)          # "as-of" date of the dataset
HISTORY_START = date(2016, 1, 1)    # earliest project start
DEFAULT_EMPLOYEES = 10_000
DEFAULT_SEED = 42

# Residency / rotation rules (per account; same default everywhere for now)
ROTATION_THRESHOLD_MONTHS = 24
COOL_OFF_MONTHS = 6

# Target shares of the current workforce (delivery employees only)
TARGET_BENCH_SHARE = 0.12
TARGET_NOTICE_SHARE = 0.015
CORPORATE_SHARE = 0.025             # HR / Talent Acquisition / RMG

# ---------------------------------------------------------------- IBUs
# key: (display name, headcount weight, domain-flavour words used in project names)
IBUS = {
    "BFS":   ("Banking & Financial Services", 0.20, ["Retail Banking", "Payments", "Lending", "Wealth", "Risk & Compliance", "Cards"]),
    "INS":   ("Insurance", 0.13, ["Policy", "Claims", "Underwriting", "Billing", "Agent Portal"]),
    "HLS":   ("Healthcare & Life Sciences", 0.14, ["Claims", "Patient Engagement", "Clinical Data", "Pharmacy", "Provider Network"]),
    "TTA":   ("Travel, Transportation & Aviation", 0.12, ["Reservations", "Crew Management", "Loyalty", "Flight Ops", "Cargo"]),
    "RCG":   ("Retail & Consumer Goods", 0.11, ["E-commerce", "Store Ops", "Supply Chain", "Merchandising", "Loyalty"]),
    "MFG":   ("Manufacturing & Automotive", 0.09, ["Shop Floor", "Connected Vehicle", "Supply Chain", "Quality", "Dealer Network"]),
    "ENU":   ("Energy & Utilities", 0.07, ["Smart Metering", "Grid Ops", "Customer Billing", "Asset Management"]),
    "AGR":   ("Agriculture & Agritech", 0.06, ["Farm Insights", "Agri Supply Chain", "Crop Analytics", "Farmer Portal"]),
    "TMT":   ("Telecom & Media", 0.08, ["OSS/BSS", "Network", "Subscriber", "Billing", "Content Platform"]),
}

# ---------------------------------------------------------------- Grades
# grade: (pyramid weight, min exp yrs, max exp yrs)
GRADES = {
    "A1": (0.10, 0.0, 1.5),
    "A2": (0.28, 1.0, 4.0),
    "A3": (0.24, 3.0, 6.0),
    "B1": (0.16, 5.0, 9.0),
    "B2": (0.10, 8.0, 12.0),
    "C1": (0.06, 11.0, 15.0),
    "C2": (0.035, 14.0, 19.0),
    "D1": (0.015, 18.0, 23.0),
    "D2": (0.01, 22.0, 28.0),
}
GRADE_ORDER = list(GRADES)

# ---------------------------------------------------------------- Locations
OFFSHORE_CITIES = {
    "Noida": 0.20, "Bengaluru": 0.20, "Hyderabad": 0.17, "Pune": 0.14,
    "Chennai": 0.12, "Bhubaneswar": 0.07, "Kolkata": 0.05, "Mumbai": 0.05,
}
ONSITE_CITIES = {
    "USA": ["New York", "Dallas", "Atlanta", "Chicago", "Boston"],
    "UK": ["London", "Manchester"],
    "Europe": ["Frankfurt", "Amsterdam", "Zurich"],
    "APAC": ["Singapore", "Sydney", "Tokyo"],
    "India": ["Mumbai", "Bengaluru"],
}
CLIENT_COUNTRY_WEIGHTS = {"USA": 0.50, "UK": 0.20, "Europe": 0.15, "APAC": 0.10, "India": 0.05}
ONSITE_SHARE = 0.08

WORK_MODES = {"Hybrid": 0.60, "Work from Office": 0.25, "Remote": 0.15}
SHIFTS = {"General (IST)": 0.55, "UK Overlap": 0.20, "US Overlap": 0.22, "24x7 Rotational": 0.03}
