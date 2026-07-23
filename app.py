import streamlit as st
import pdfplumber
import pandas as pd
import re

st.set_page_config(page_title="MCA Bank Parser & Underwriting Tool", page_icon="💳", layout="wide")

st.title("💳 MCA Auto-Parser & Underwriting Engine")
st.caption("Upload 6–10 bank statement PDFs to calculate true monthly revenue and identify existing MCA positions.")

st.divider()

# --- LENDER DICTIONARY WITH ALIASES & TIERS ---
KNOWN_FUNDERS = {
    # Premium Lenders
    "Merchant Growth": {"tier": "Premium", "keywords": ["MERCHPAD", "MERCH PAD", "MERCHANT GROWTH"]},
    "Greenbox": {"tier": "Premium", "keywords": ["GREENBOX", "GREEN BOX", "GREENBOX CAPITAL"]},
    "Vault": {"tier": "Premium", "keywords": ["VAULT", "VAULT FINANCIAL"], "restrict_freq": ["DAILY", "WEEKLY"]},
    "Driven": {"tier": "Premium", "keywords": ["DRIVEN", "DRIVEN CAPITAL"]},
    "Journey": {"tier": "Premium", "keywords": ["JOURNEY CAPITAL", "JOURNEY", "JOURNEY FUNDING", "ONDECK"]},
    "iCapital": {"tier": "Premium", "keywords": ["ICAPITAL", "I CAPITAL", "I-CAPITAL"]},

    # Standard Lenders
    "Canacap": {"tier": "Standard", "keywords": ["CANA CAP", "CANACAP", "CANA CAPITAL", "CANACAPITAL"]},
    "2M7": {"tier": "Standard", "keywords": ["2M7", "URAL", "URAL CAPITAL", "2M7 FINANCIAL"]},
    "Bizfund": {"tier": "Standard", "keywords": ["BIZFUND", "BIZ FUND", "BIZ-FUND"]},
    "Xuper": {"tier": "Standard", "keywords": ["XUPER", "XUPER FUNDING", "XUPER CAPITAL"]},
    "Newco": {"tier": "Standard", "keywords": ["NEWCO", "NEWCO CAPITAL"]},
    "Sheaves": {"tier": "Standard", "keywords": ["SHEAVES", "SHEAVES CAPITAL"]},
    "CMCA": {"tier": "Standard", "keywords": ["CMCA", "C.M.C.A.", "CANADIAN MERCHANT"]},
    "B2B": {"tier": "Standard", "keywords": ["B2B CAPITAL", "B2B FUNDING", "B2B"]},
    "Forward Funding": {"tier": "Standard", "keywords": ["FORWARD FUNDING", "FORWARD-FUNDING", "FORWARD FUND", "FORWARD CAPITAL"]},
    "KM Capital": {"tier": "Standard", "keywords": ["KM CAPITAL", "2313833 ONTARIO", "2313833 ONTARIO INC"]},
    "EFSA": {"tier": "Standard", "keywords": ["EFSA", "EFSA CAPITAL"]},
    "Rook Bristol / Elect": {"tier": "Standard", "keywords": ["ROOK BRISTOL", "ELECT CAPITAL", "ROOKBRISTOL"]},
    "Sharp Shooter Funding": {"tier": "Standard", "keywords": ["SHARP SHOOTER", "SHARPSHOOTER", "SSF"]},
    "Mfund": {"tier": "Standard", "keywords": ["MFUND", "M-FUND", "M FUND"]},
    "Quebec Inc (9341-8812)": {"tier": "Standard", "keywords": ["9341-8812", "9341 8812", "93418812 QUEBEC"]},
    "North Funding": {"tier": "Standard", "keywords": ["NORTH FUNDING", "NORTHFUNDING"]},
    "Business Credit Capital": {"tier": "Standard", "keywords": ["BUSINESS CR", "BCC", "BUSINESS CREDIT CAPITAL"]},
    "Flex Capital Group": {"tier": "Standard", "keywords": ["FLEXCAPITALGROUP", "FLEX CAPITAL", "FLEX CAPITAL GROUP"]},
    "ONTAP Capital": {"tier": "Standard", "keywords": ["ONTAP", "ONTAP CAPITAL", "ON TAP CAPITAL"]},
    "Clara Capital": {"tier": "Standard", "keywords": ["CLARA CAPITAL", "CLARA"]},
    "FUNDFI": {"tier": "Standard", "keywords": ["FUNDFI", "FUND FI", "FUND-FI"]}
}

# --- SECTION 1: BANK STATEMENT PDF UPLOADER ---
st.subheader("1. Bank Statement Ingestion")

uploaded_files = st.file_uploader(
    "Upload 6-10 Monthly Bank Statements (PDF format)", 
    type=["pdf"], 
    accept_multiple_files=True
)

auto_monthly_revenue = 0.0
detected_positions = []

if uploaded_files:
    st.info(f"📁 **{len(uploaded_files)} Statements Uploaded.** Processing transactions...")
    
    total_deposits = 0.0
    detected_ach_debits = []
    
    for pdf_file in uploaded_files:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                
                lines = text.split("\n")
                for line in lines:
                    line_upper = line.upper()
                    
                    # 1. Deposit / Revenue Detection
                    if any(term in line_upper for term in ["DEPOSIT", "CREDIT", "ACH IN", "SQUARE", "STRIPE", "CLOVER", "E-TRANSFER"]):
                        if not any(excl in line_upper for excl in ["TRANSFER", "REFUND", "LOAN PROCEEDS", "LINE OF CREDIT"]):
                            amounts = re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2}", line)
                            if amounts:
                                total_deposits += float(amounts[-1].replace(",", ""))

                    # 2. Lender ACH Debit Detection
                    for lender_name, meta in KNOWN_FUNDERS.items():
                        # Check if any keyword matches
                        if any(kw in line_upper for kw in meta["keywords"]):
                            # Frequency Filter check for Vault
                            if "restrict_freq" in meta:
                                is_monthly = "MONTHLY" in line_upper
                                if is_monthly:
                                    continue # Skip monthly Vault transactions
                            
                            if any(term in line_upper for term in ["ACH", "DEBIT", "WITHDRAWAL", "PRE", "LNS"]):
                                amounts = re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2}", line)
                                if amounts:
                                    debit_amt = float(amounts[-1].replace(",", ""))
                                    detected_ach_debits.append({
                                        "Lender": lender_name,
                                        "Tier": meta["tier"],
                                        "Amount": debit_amt,
                                        "RawLine": line[:60]
                                    })

    num_months = max(1, len(uploaded_files))
    auto_monthly_revenue = total_deposits / num_months
    
    st.success(f"✅ **Extraction Complete:** Estimated Average Monthly Revenue: **${auto_monthly_revenue:,.2f}** ({num_months} month average)")

st.divider()

# --- SECTION 2: UNDERWRITING INPUTS & OVERRIDES ---
col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.subheader("2. Financials & Positions")
    
    avg_monthly_rev = st.number_input(
        "Average Monthly Revenue ($)", 
        min_value=1000.0, 
        value=float(auto_monthly_revenue if auto_monthly_revenue > 0 else 50000.0), 
        step=1000.0
    )

    st.markdown("#### Active Debt Positions")
    st.caption("Auto-populated from detected lender ACHs. Edit amounts or add additional positions as needed.")

    if "positions" not in st.session_state:
        st.session_state.positions = [{"amount": 150.0, "freq": "Daily"}]

    # Auto-populate session state if funders were detected
    if detected_ach_debits and len(st.session_state.positions) == 1 and st.session_state.positions[0]["amount"] == 150.0:
        st.session_state.positions = []
        for ach in detected_ach_debits[:5]:
            st.session_state.positions.append({"amount": ach["Amount"], "freq": "Daily"})

    total_existing_monthly_debt = 0.0
    num_positions = len(st.session_state.positions)

    for i, pos in enumerate(st.session_state.positions):
        c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
        with c1:
            st.session_state.positions[i]["amount"] = st.number_input(
                f"Position #{i+1} Amount ($)", 
                min_value=0.0, 
                value=float(pos["amount"]), 
                step=25.0, 
                key=f"amt_{i}"
            )
        with c2:
            st.session_state.positions[i]["freq"] = st.selectbox(
                f"Frequency #{i+1}", 
                ["Daily", "Weekly"], 
                index=0 if pos["freq"] == "Daily" else 1, 
                key=f"freq_{i}"
            )
        
        amt = st.session_state.positions[i]["amount"]
        pos_monthly = amt * 21.67 if st.session_state.positions[i]["freq"] == "Daily" else amt * 4.33
        pos_dsr_pct = (pos_monthly / avg_monthly_rev * 100) if avg_monthly_rev > 0 else 0.0
        total_existing_monthly_debt += pos_monthly

        with c3:
            st.metric(f"Pos #{i+1} Cost", f"${pos_monthly:,.0f}/mo", f"{pos_dsr_pct:.1f}% DSR")

        with c4:
            st.write("")
            if st.button("🗑️", key=f"del_{i}"):
                st.session_state.positions.pop(i)
                st.rerun()

    if st.button("➕ Add Debt Position"):
        st.session_state.positions.append({"amount": 100.0, "freq": "Daily"})
        st.rerun()

    st.subheader("3. Qualitative Risk Factors")
    credit_score = st.slider("FICO Credit Score", 500, 850, 640, step=5)
    tib_months = st.number_input("Time in Business (Months)", min_value=1, value=24, step=1)
    
    industry_type = st.selectbox(
        "Industry Risk Tier",
        options=["Low Risk (Medical, Professional Services)", 
                 "Medium Risk (Retail, Wholesalers)", 
                 "High Risk (Trucking, Construction, Restaurants)"]
    )
    
    has_bk_collections = st.checkbox("Active Bankruptcy or Open Major Collections?")

    st.subheader("4. Underwriting Parameters")
    target_dsr_cap = st.slider("Max Debt Service Ratio (DSR) Cap", 10, 45, 35) / 100.0
    factor_rate = st.number_input("Target Factor Rate", min_value=1.05, max_value=1.60, value=1.49, step=0.01)

# --- SECTION 3: UNDERWRITING ENGINE & DECISION ---
existing_dsr = (total_existing_monthly_debt / avg_monthly_rev) if avg_monthly_rev > 0 else 0.0

risk_reasons = []
risk_multiplier = 1.0

if credit_score < 580:
    risk_multiplier *= 0.65
    risk_reasons.append(f"Credit Score: {credit_score} (Sub-580 FICO — 35% penalty)")
elif credit_score < 650:
    risk_multiplier *= 0.85
    risk_reasons.append(f"Credit Score: {credit_score} (Moderate FICO — 15% penalty)")
else:
    risk_reasons.append(f"Credit Score: {credit_score} (Prime FICO — No penalty)")

if tib_months < 12:
    risk_multiplier *= 0.70
    risk_reasons.append(f"Time in Business: {tib_months}m (<1 Year — 30% penalty)")
elif tib_months < 24:
    risk_multiplier *= 0.85
    risk_reasons.append(f"Time in Business: {tib_months}m (<2 Years — 15% penalty)")
else:
    risk_reasons.append(f"Time in Business: {tib_months}m (>2 Years — No penalty)")

if "High Risk" in industry_type:
    risk_multiplier *= 0.80
    risk_reasons.append("Industry: High Risk Sector — 20% penalty")

if has_bk_collections:
    risk_reasons.append("Bankruptcy / Collections: ACTIVE ON RECORD (Decline)")

position_penalty = 1.0
if num_positions == 2:
    position_penalty = 0.85
    risk_reasons.append("Active Positions: 2 Positions (15% penalty)")
elif num_positions == 3:
    position_penalty = 0.70
    risk_reasons.append("Active Positions: 3 Positions (30% penalty)")
elif num_positions >= 4:
    position_penalty = 0.50
    risk_reasons.append(f"Active Positions: {num_positions} Positions (50% max penalty)")

final_risk_multiplier = risk_multiplier * position_penalty

max_allowable_monthly_debt = avg_monthly_rev * target_dsr_cap
net_available_monthly = (max_allowable_monthly_debt - total_existing_monthly_debt) * final_risk_multiplier

with col_right:
    st.subheader("Underwriting Decision")
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Monthly Debt", f"${total_existing_monthly_debt:,.2f}")
    m2.metric("Current DSR", f"{existing_dsr*100:.1f}%")
    m3.metric("Active Positions", f"{num_positions}")

    st.divider()

    if has_bk_collections or existing_dsr >= target_dsr_cap or net_available_monthly <= 0:
        st.error("❌ **DECISION: DECLINED**")
    else:
        st.success("✅ **DECISION: APPROVED**")

        selected_term = st.radio(
            "Focus Offer Term:", [4, 5, 6, 7, 8], index=2, format_func=lambda x: f"{x} Months", horizontal=True
        )

        offer_data = []
        for term in range(4, 9):
            total_repayment = net_available_monthly * term
            funding_amount = total_repayment / factor_rate
            daily_payment = net_available_monthly / 21.67
            weekly_payment = net_available_monthly / 4.33

            is_selected = "👈 Selected" if term == selected_term else ""
            offer_data.append({
                "Term": f"{term} Months {is_selected}",
                "Funding Offer ($)": f"${funding_amount:,.2f}",
                "Total Payback ($)": f"${total_repayment:,.2f}",
                "Daily ACH": f"${daily_payment:,.2f}",
                "Weekly ACH": f"${weekly_payment:,.2f}"
            })

        st.dataframe(offer_data, use_container_width=True, hide_index=True)

        sel_repayment = net_available_monthly * selected_term
        sel_funding = sel_repayment / factor_rate

        summary_text = f"""*** UNDERWRITING DECISION & OFFER SUMMARY ***
Status: APPROVED
Selected Offer: ${sel_funding:,.2f} for {selected_term} Months
Target Factor Rate: {factor_rate:.2f}
Total Payback: ${sel_repayment:,.2f}

Financial Metrics:
- Parsed Average Monthly Revenue: ${avg_monthly_rev:,.2f}
- Active Debt Positions: {num_positions} position(s)
- Total Existing Monthly Debt: ${total_existing_monthly_debt:,.2f}
- Pre-Funding DSR: {existing_dsr*100:.1f}% (Max Cap: {target_dsr_cap*100:.0f}%)
- Combined Risk Multiplier: {final_risk_multiplier:.2f}x

Qualitative Audit:
"""
        for reason in risk_reasons:
            summary_text += f"- {reason}\n"

        st.info(summary_text)