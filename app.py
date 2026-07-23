import streamlit as st
import pdfplumber
import pandas as pd
import re
from collections import defaultdict

st.set_page_config(page_title="MCA Bank Parser & Underwriting Tool", page_icon="💳", layout="wide")

st.title("💳 MCA Statement Analyzer & Underwriting Engine")
st.caption("Upload bank statements to group transactions, separate total deposits from true revenue, isolate MCA positions, and calculate risk metrics.")
st.divider()

# --- LENDER DICTIONARY WITH ALIASES & TIERS ---
KNOWN_FUNDERS = {
    "Merchant Growth": {"tier": "Premium", "keywords": ["MERCHPAD", "MERCH PAD", "MERCHANT GROWTH"]},
    "Greenbox": {"tier": "Premium", "keywords": ["GREENBOX", "GREEN BOX", "GREENBOX CAPITAL"]},
    "Vault": {"tier": "Premium", "keywords": ["VAULT", "VAULT FINANCIAL"]},
    "Driven": {"tier": "Premium", "keywords": ["DRIVEN", "DRIVEN CAPITAL", "DRIVEN FINANCIAL"]},
    "Journey": {"tier": "Premium", "keywords": ["JOURNEY CAPITAL", "JOURNEY", "JOURNEY FUNDING", "ONDECK"]},
    "iCapital": {"tier": "Premium", "keywords": ["ICAPITAL", "I CAPITAL", "I-CAPITAL"]},
    "Canacap": {"tier": "Standard", "keywords": ["CANA CAP", "CANACAP", "CANA CAPITAL", "CANACAPITAL"]},
    "2M7": {"tier": "Standard", "keywords": ["2M7", "URAL", "URAL CAPITAL", "2M7 FINANCIAL"]},
    "Bizfund": {"tier": "Standard", "keywords": ["BIZFUND", "BIZ FUND", "BIZ-FUND"]},
    "Xuper": {"tier": "Standard", "keywords": ["XUPER", "XUPER FUNDING", "XUPER CAPITAL"]},
    "Newco": {"tier": "Standard", "keywords": ["NEWCO", "NEWCO CAPITAL"]},
    "Sheaves": {"tier": "Standard", "keywords": ["SHEAVES", "SHEAVES CAPITAL"]},
    "CMCA": {"tier": "Standard", "keywords": ["CMCA", "C.M.C.A.", "CANADIAN MERCHANT"]},
    "B2B": {"tier": "Standard", "keywords": ["B2B CAPITAL", "B2B FUNDING", "B2B"]},
    "Forward Funding": {"tier": "Standard", "keywords": ["FORWARD FUNDING", "FORWARD-FUNDING", "FORWARD FUND"]},
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

# --- PARSING FILTERS & REGEX ---
NSF_KEYWORDS = ["NSF FEE", "NSF CHARGE", "NON-SUFFICIENT", "OVERDRAFT", "RETURNED ITEM", "OVERDRAWN"]

JUNK_LINES = [
    "ACCOUNT SUMMARY", "TOTAL AMOUNT", "NO. OF DEBITS", "NO. OF CREDITS", 
    "ITEM VOLUME", "TOTAL SERVICE CHARGES", "UNCOLLECTED FEES", "PLEASE EXAMINE", 
    "GST REGISTRATION", "REGISTERED TRADEMARK", "ACCOUNT DETAILS", 
    "WITHDRAWALS/DEBITS", "DEPOSITS/CREDITS", "BALANCE ($)", "STATEMENT OF"
]

REVENUE_EXCLUSIONS = [
    "INTERNAL TRANSFER", "TRANSFER FROM", "TRSF FROM", "MEMO TRANSFER", "ACCOUNT TRANSFER", 
    "LOAN", "BDC HASCAP", "LINE OF CREDIT", "LOC DRAW", "CASH ADVANCE", "ADVANCE PROCEEDS", 
    "REVERSAL", "REFUND", "RETURNED", "RTN WIRE", "PAYROLL", "UNITED TRADING", "ACCOUNTS PAYABLE"
]
for f_data in KNOWN_FUNDERS.values():
    REVENUE_EXCLUSIONS.extend(f_data["keywords"])

# Regex pattern for MM/DD/YYYY, YYYY-MM-DD, DD-MMM-YYYY, MM/DD/YY
DATE_REGEX = r"^(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{2}-[A-Za-z]{3}-\d{4}|\d{2}/\d{2}/\d{2})"

# --- SECTION 1: BANK STATEMENT PDF UPLOADER ---
st.subheader("1. Bank Statement Ingestion & Month-by-Month Analysis")

uploaded_files = st.file_uploader(
    "Upload Bank Statements (PDFs)", 
    type=["pdf"], 
    accept_multiple_files=True
)

auto_monthly_revenue = 0.0
total_nsf_count = 0
detected_funder_positions = []

monthly_data_store = defaultdict(lambda: {
    "Start Balance": 0.0, "Stated Credits": 0.0, "True Revenue": 0.0, 
    "Stated Debits": 0.0, "End Balance": 0.0, "NSF Count": 0
})
mca_tracker = defaultdict(lambda: {"total_amount": 0.0, "debit_count": 0})

if uploaded_files:
    st.info("📁 **Processing Documents...** Grouping transactions and reconciling balances.")
    
    running_balance = None

    for pdf_file in uploaded_files:
        with pdfplumber.open(pdf_file) as pdf:
            transactions = []
            current_tx = []
            
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                
                lines = text.split("\n")
                for line in lines:
                    line_clean = line.strip()
                    if not line_clean: continue
                    
                    upper_line = line_clean.upper()
                    
                    if any(junk in upper_line for junk in JUNK_LINES):
                        continue
                        
                    # Match dates across formats
                    if re.match(DATE_REGEX, line_clean):
                        if current_tx:
                            transactions.append(" ".join(current_tx))
                        current_tx = [line_clean]
                    elif current_tx:
                        if "SCOTIABANK" in upper_line or "P.O. BOX" in upper_line or "PAGE " in upper_line:
                            continue
                        current_tx.append(line_clean)
            
            if current_tx:
                transactions.append(" ".join(current_tx))

            # Process isolated transaction blocks
            for tx in transactions:
                tx_upper = tx.upper()
                
                # Extract date string
                date_match = re.match(DATE_REGEX, tx)
                if not date_match: continue
                
                raw_date_str = date_match.group(1)
                try:
                    parsed_dt = pd.to_datetime(raw_date_str, format='mixed', dayfirst=False)
                    month_label = parsed_dt.strftime("%b %Y")  # e.g., "Jan 2025"
                except Exception:
                    continue

                raw_amounts = re.findall(r"(?:^|\s)\$?(\d{1,3}(?:,\d{3})*\.\d{2}-?)(?=\s|$)", tx_upper)
                
                amounts = []
                for a in raw_amounts:
                    clean_a = a.replace(",", "")
                    if clean_a.endswith("-"):
                        clean_a = "-" + clean_a[:-1]
                    amounts.append(float(clean_a))
                
                if not amounts: continue

                if "BALANCE FORWARD" in tx_upper or "OPENING BALANCE" in tx_upper:
                    running_balance = amounts[-1]
                    if monthly_data_store[month_label]["Start Balance"] == 0.0:
                        monthly_data_store[month_label]["Start Balance"] = running_balance
                    continue

                primary_amount = 0.0
                balance_amount = None

                if len(amounts) >= 2:
                    balance_amount = amounts[-1]
                elif len(amounts) == 1:
                    primary_amount = amounts[0]

                is_credit = False
                is_debit = False

                # Balance Math Proof
                if running_balance is not None and balance_amount is not None:
                    diff = round(balance_amount - running_balance, 2)
                    
                    for amt in amounts[:-1]:
                        if abs(diff - amt) < 0.05:
                            is_credit = True
                            primary_amount = amt
                            break
                        elif abs(diff - (-amt)) < 0.05:
                            is_debit = True
                            primary_amount = amt
                            break

                # Fallback Keyword Matching
                if not is_credit and not is_debit:
                    primary_amount = amounts[-2] if len(amounts) >= 2 else amounts[0]
                    if any(kw in tx_upper for kw in ["CREDIT", "DEPOSIT", "INCOMING", "E-TRANSFER", "PAYABLE", "RTN WIRE"]):
                        is_credit = True
                        if primary_amount < 0: primary_amount = abs(primary_amount)
                    elif any(kw in tx_upper for kw in ["DEBIT", "PAYMENT", "PAD", "WITHDRAWAL", "FEE", "OUTGOING", "CHQ", "CHEQUE", "CHARGE", "LEASE", "PURCHASE"]):
                        is_debit = True
                        if primary_amount < 0: primary_amount = abs(primary_amount)

                # Categorization
                if is_credit:
                    monthly_data_store[month_label]["Stated Credits"] += primary_amount
                    if not any(excl in tx_upper for excl in REVENUE_EXCLUSIONS):
                        monthly_data_store[month_label]["True Revenue"] += primary_amount
                        
                elif is_debit:
                    monthly_data_store[month_label]["Stated Debits"] += primary_amount
                    if any(kw in tx_upper for kw in NSF_KEYWORDS):
                        monthly_data_store[month_label]["NSF Count"] += 1
                        total_nsf_count += 1
                        
                    for lender_name, meta in KNOWN_FUNDERS.items():
                        if any(kw in tx_upper for kw in meta["keywords"]):
                            mca_tracker[lender_name]["total_amount"] += primary_amount
                            mca_tracker[lender_name]["debit_count"] += 1
                            break 
                
                if balance_amount is not None and (is_credit or is_debit):
                    running_balance = balance_amount
                    monthly_data_store[month_label]["End Balance"] = balance_amount

    # Build Final Tables & Averages
    num_active_months = max(1, len(monthly_data_store))
    chart_data = []
    total_true_revenue = 0.0
    total_stated_credits = 0.0
    total_stated_debits = 0.0
    
    for month, data in monthly_data_store.items():
        chart_data.append({
            "Month": month,
            "Start Balance ($)": data["Start Balance"],
            "Stated Credits ($)": data["Stated Credits"],
            "True Revenue ($)": data["True Revenue"],
            "Stated Debits ($)": data["Stated Debits"],
            "NSF Fees": data["NSF Count"],
            "End Balance ($)": data["End Balance"]
        })
        total_true_revenue += data["True Revenue"]
        total_stated_credits += data["Stated Credits"]
        total_stated_debits += data["Stated Debits"]

    df_breakdown = pd.DataFrame(chart_data)
    auto_monthly_revenue = total_true_revenue / num_active_months
    avg_monthly_credits = total_stated_credits / num_active_months
    avg_monthly_debits = total_stated_debits / num_active_months
    avg_nsf_per_month = total_nsf_count / num_active_months

    # Evaluate MCA Funder Frequency & Monthly Impact
    for lender, data in mca_tracker.items():
        avg_debits_per_month = data["debit_count"] / num_active_months
        freq = "Daily" if avg_debits_per_month > 8 else "Weekly"
        divisor = 21.67 if freq == "Daily" else 4.33
        
        avg_monthly_impact = data["total_amount"] / num_active_months
        payment_amount = avg_monthly_impact / divisor
        
        detected_funder_positions.append({
            "name": lender,
            "amount": round(payment_amount, 2),
            "freq": freq,
            "monthly_avg": round(avg_monthly_impact, 2)
        })

    st.markdown("### 📊 Extracted Monthly Financial Breakdown")
    st.dataframe(
        df_breakdown.style.format({
            "Start Balance ($)": "${:,.2f}",
            "Stated Credits ($)": "${:,.2f}",
            "True Revenue ($)": "${:,.2f}",
            "Stated Debits ($)": "${:,.2f}",
            "End Balance ($)": "${:,.2f}",
            "NSF Fees": "{:,.0f}"
        }), 
        use_container_width=True, hide_index=True
    )

    # Visual Bar Chart: Deposits vs True Revenue
    st.markdown("### 📈 Stated Deposits vs. True Revenue Comparison")
    if not df_breakdown.empty:
        chart_df = df_breakdown.set_index("Month")[["Stated Credits ($)", "True Revenue ($)"]]
        st.bar_chart(chart_df)

    st.markdown("### 📌 Multi-Month Overview & Averages")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active Months", f"{num_active_months} Months")
    c2.metric("Avg Monthly Deposits", f"${avg_monthly_credits:,.2f}", f"${total_stated_credits:,.2f} Total")
    c3.metric("Avg True Monthly Rev", f"${auto_monthly_revenue:,.2f}", f"${total_true_revenue:,.2f} Total")
    c4.metric("Avg Monthly Debits", f"${avg_monthly_debits:,.2f}", f"${total_stated_debits:,.2f} Total")
    c5.metric("NSF Fees", f"{total_nsf_count} Total", f"{avg_nsf_per_month:.1f} / mo", delta_color="inverse" if total_nsf_count > 0 else "normal")

st.divider()

# --- SECTION 2: UNDERWRITING INPUTS & OVERRIDES ---
col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.subheader("2. Financials & Position Overrides")
    
    avg_monthly_rev = st.number_input(
        "Average Monthly True Revenue ($)", 
        min_value=1000.0, 
        value=float(auto_monthly_revenue if auto_monthly_revenue > 0 else 50000.0), 
        step=1000.0
    )

    st.markdown("#### Detected Debt Positions")
    st.caption("Funder, frequency, and payment amount dynamically calculated from statement history.")

    if "positions" not in st.session_state or uploaded_files:
        if detected_funder_positions:
            st.session_state.positions = [
                {"name": f["name"], "amount": f["amount"], "freq": f["freq"]}
                for f in detected_funder_positions
            ]
        elif "positions" not in st.session_state:
            st.session_state.positions = [{"name": "Existing Funder #1", "amount": 150.0, "freq": "Daily"}]

    total_existing_monthly_debt = 0.0
    num_positions = len(st.session_state.positions)

    # Safe delete handling via state indices
    to_delete = None
    for i, pos in enumerate(st.session_state.positions):
        st.markdown(f"**Position #{i+1}**")
        p1, p2, p3, p4 = st.columns([2.5, 2, 2, 1])
        
        with p1:
            st.session_state.positions[i]["name"] = st.text_input(f"Lender Name #{i+1}", value=pos.get("name", ""), key=f"name_{i}")
        with p2:
            st.session_state.positions[i]["amount"] = st.number_input(f"Payment Amount ($) #{i+1}", min_value=0.0, value=float(pos["amount"]), step=25.0, key=f"amt_{i}")
        with p3:
            st.session_state.positions[i]["freq"] = st.selectbox(f"Frequency #{i+1}", ["Daily", "Weekly"], index=0 if pos["freq"] == "Daily" else 1, key=f"freq_{i}")
        
        amt = st.session_state.positions[i]["amount"]
        pos_monthly = amt * 21.67 if st.session_state.positions[i]["freq"] == "Daily" else amt * 4.33
        pos_dsr_pct = (pos_monthly / avg_monthly_rev * 100) if avg_monthly_rev > 0 else 0.0
        total_existing_monthly_debt += pos_monthly

        with p4:
            st.write("")
            if st.button("🗑️", key=f"del_{i}"):
                to_delete = i

        st.caption(f"Monthly Impact: **${pos_monthly:,.2f}/mo** | **{pos_dsr_pct:.1f}% DSR**")
        st.write("---")

    if to_delete is not None:
        st.session_state.positions.pop(to_delete)
        st.rerun()

    if st.button("➕ Add Debt Position"):
        st.session_state.positions.append({"name": "New Funder", "amount": 100.0, "freq": "Daily"})
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

# --- SECTION 3: UNDERWRITING DECISION ENGINE ---
existing_dsr = (total_existing_monthly_debt / avg_monthly_rev) if avg_monthly_rev > 0 else 0.0

risk_reasons = []
risk_multiplier = 1.0

if 'avg_nsf_per_month' in locals():
    if avg_nsf_per_month > 3.0:
        risk_multiplier *= 0.70
        risk_reasons.append(f"NSF Fee Risk: High ({total_nsf_count} total, {avg_nsf_per_month:.1f}/mo — 30% penalty)")
    elif avg_nsf_per_month > 1.0:
        risk_multiplier *= 0.85
        risk_reasons.append(f"NSF Fee Risk: Moderate ({total_nsf_count} total, {avg_nsf_per_month:.1f}/mo — 15% penalty)")
    else:
        risk_reasons.append(f"NSF Fee Risk: Clean Record ({total_nsf_count} total fees — No penalty)")

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
else:
    risk_reasons.append("Industry: Standard Risk Sector — No penalty")

if has_bk_collections:
    risk_reasons.append("Bankruptcy / Collections: ACTIVE ON RECORD (Hard Decline)")
else:
    risk_reasons.append("Bankruptcy / Collections: Clean Record")

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
else:
    risk_reasons.append("Active Positions: 1 Position (Clean — No penalty)")

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
        if has_bk_collections:
            st.write("**Reasoning:** Active Bankruptcy or Open Major Collections present.")
        elif existing_dsr >= target_dsr_cap:
            st.write(f"**Reasoning:** Existing DSR ({existing_dsr*100:.1f}%) exceeds maximum threshold ({target_dsr_cap*100:.0f}%).")
        else:
            st.write("**Reasoning:** Zero net funding capacity available after applying risk penalties.")
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
        sel_daily = net_available_monthly / 21.67
        sel_weekly = net_available_monthly / 4.33

        st.markdown("### 📋 Executive Underwriting Summary")

        positions_summary_str = ""
        for p in st.session_state.positions:
            positions_summary_str += f"  - {p['name']}: ${p['amount']:,.2f} ({p['freq']})\n"

        summary_text = f"""*** UNDERWRITING DECISION & OFFER SUMMARY ***
Status: APPROVED
Selected Offer: ${sel_funding:,.2f} for {selected_term} Months
Target Factor Rate: {factor_rate:.2f}
Total Payback: ${sel_repayment:,.2f}
Payment Schedule: ${sel_daily:,.2f}/day OR ${sel_weekly:,.2f}/week

Financial Metrics:
- Avg True Monthly Revenue: ${avg_monthly_rev:,.2f}
- Active Debt Positions ({num_positions}):
{positions_summary_str}- Total Existing Monthly Debt: ${total_existing_monthly_debt:,.2f}
- Pre-Funding DSR: {existing_dsr*100:.1f}% (Max Cap: {target_dsr_cap*100:.0f}%)
- Combined Risk Multiplier: {final_risk_multiplier:.2f}x

Qualitative Audit:
"""
        for reason in risk_reasons:
            summary_text += f"- {reason}\n"

        st.info(summary_text)

        st.download_button(
            label=f"📄 Download Summary ({selected_term}-Month Offer)",
            data=summary_text,
            file_name=f"underwriting_summary_{selected_term}m.txt",
            mime="text/plain"
        )
