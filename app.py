import streamlit as st
import pdfplumber
import pandas as pd
import re
from collections import defaultdict

st.set_page_config(page_title="MCA Bank Parser & Underwriting Tool", page_icon="💳", layout="wide")

st.title("💳 MCA Statement Analyzer & Underwriting Engine")
st.caption("Upload bank statements (individual or merged). The engine isolates true revenue, ignores running balances, and calculates MCA positions.")
st.divider()

# --- LENDER DICTIONARY WITH ALIASES & TIERS ---
KNOWN_FUNDERS = {
    "Merchant Growth": {"tier": "Premium", "keywords": ["MERCHPAD", "MERCH PAD", "MERCHANT GROWTH"]},
    "Greenbox": {"tier": "Premium", "keywords": ["GREENBOX", "GREEN BOX", "GREENBOX CAPITAL"]},
    "Vault": {"tier": "Premium", "keywords": ["VAULT", "VAULT FINANCIAL"]},
    "Driven": {"tier": "Premium", "keywords": ["DRIVEN", "DRIVEN CAPITAL"]},
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
MONTH_MAP = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun", 
             "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
             "JAN": "Jan", "FEB": "Feb", "MAR": "Mar", "APR": "Apr", "MAY": "May", "JUN": "Jun",
             "JUL": "Jul", "AUG": "Aug", "SEP": "Sep", "OCT": "Oct", "NOV": "Nov", "DEC": "Dec"}

NSF_KEYWORDS = ["NSF FEE", "NSF CHARGE", "NON-SUFFICIENT FEE", "OVERDRAFT FEE", "RETURNED ITEM FEE"]

# Strict exclusions for True Revenue (Internal Transfers, Loans, Refunds, Account Owner Names)
REVENUE_EXCLUSIONS = [
    "INTERNAL TRANSFER", "TRANSFER FROM", "TRSF FROM", "MEMO TRANSFER", "ACCOUNT TRANSFER", 
    "LOAN", "BDC HASCAP", "LINE OF CREDIT", "LOC DRAW", "CASH ADVANCE", "ADVANCE PROCEEDS", 
    "REVERSAL", "REFUND", "RETURNED", "RTN WIRE", "PAYROLL", "UNITED TRADING"
]
# Add all MCA keywords to exclusions so funding deposits aren't counted as organic revenue
for f_data in KNOWN_FUNDERS.values():
    REVENUE_EXCLUSIONS.extend(f_data["keywords"])


# --- SECTION 1: BANK STATEMENT PDF UPLOADER ---
st.subheader("1. Bank Statement Ingestion & Month-by-Month Analysis")

uploaded_files = st.file_uploader(
    "Upload Bank Statements (4-12 PDFs or 1 Merged PDF)", 
    type=["pdf"], 
    accept_multiple_files=True
)

auto_monthly_revenue = 0.0
total_nsf_count = 0
detected_funder_positions = []
monthly_data_store = defaultdict(lambda: {"True Revenue": 0.0, "NSF Count": 0})
mca_tracker = defaultdict(lambda: {"total_amount": 0.0, "debit_count": 0})

if uploaded_files:
    st.info("📁 **Processing Documents...** Extracting summaries and calculating line-by-line true revenue.")
    
    current_month_label = "Unknown"
    
    for pdf_file in uploaded_files:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                
                lines = text.split("\n")
                recent_lines = [] # Rolling buffer to catch multi-line transaction descriptions
                
                for line in lines:
                    line_upper = line.upper().strip()
                    if not line_upper: continue
                    
                    # Keep a rolling window of the last 3 lines for context
                    recent_lines.append(line_upper)
                    if len(recent_lines) > 3:
                        recent_lines.pop(0)
                        
                    context_text = " ".join(recent_lines)
                    
                    # 1. Detect active month from transaction dates (e.g. 04/15 or Apr 15)
                    date_match = re.search(r"^\s*(?:(\d{2})/\d{2}|(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC))\b", line_upper)
                    if date_match:
                        matched_val = date_match.group(1) if date_match.group(1) else date_match.group(2)
                        if matched_val in MONTH_MAP:
                            current_month_label = MONTH_MAP[matched_val]

                    # 2. Extract Dollar Amounts (Handles commas, and captures numbers with OR without decimals)
                    raw_amounts = re.findall(r"(?<!\S)(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)(?!\S)", line_upper)
                    if not raw_amounts:
                        continue
                    
                    # Convert found amounts to floats
                    amounts = [float(a.replace(",", "")) for a in raw_amounts]
                    
                    # The first amount on the line is the transaction. The last is usually the balance.
                    primary_amount = amounts[0]

                    # 3. Explicitly ignore Bank Summary headers and balances
                    if any(excl in context_text for excl in ["BEGINNING BALANCE", "ENDING BALANCE", "OPENING BALANCE", "CLOSING BALANCE", "TOTAL DEPOSITS", "TOTAL CREDITS", "TOTAL DEBITS", "TOTAL WITHDRAWALS"]):
                        recent_lines = [] # Clear context buffer
                        continue

                    # 4. Classify as Debit or Credit based on Bank Verbiage
                    is_debit = any(kw in context_text for kw in ["PAD", "PAYMENT", "DEBIT", "WITHDRAWAL", "FEE", "OUTGOING", "SERVICE CHARGE", "CHQ", "CHEQUE"])
                    is_credit = any(kw in context_text for kw in ["CREDIT", "DEPOSIT", "INCOMING", "E-TRANSFER", "PAYABLE", "RTN WIRE"])
                    
                    # Fallback classification for E-Transfers (can be both, but usually credits if not specified as debit)
                    if "INTERAC E-TRANSFER" in context_text and not is_debit:
                        is_credit = True

                    # 5. Process Transaction
                    if is_debit:
                        # NSF Tracking
                        if any(kw in context_text for kw in NSF_KEYWORDS):
                            monthly_data_store[current_month_label]["NSF Count"] += 1
                            total_nsf_count += 1
                            
                        # MCA Debit Tracking
                        for lender_name, meta in KNOWN_FUNDERS.items():
                            if any(kw in context_text for kw in meta["keywords"]):
                                if "restrict_freq" in meta and "MONTHLY" in context_text:
                                    continue
                                mca_tracker[lender_name]["total_amount"] += primary_amount
                                mca_tracker[lender_name]["debit_count"] += 1
                                break # Stop searching once lender is found
                                
                    elif is_credit:
                        # True Revenue Calculation (Ensure it doesn't contain exclusions like loans/MCA)
                        if not any(excl in context_text for excl in REVENUE_EXCLUSIONS):
                            monthly_data_store[current_month_label]["True Revenue"] += primary_amount
                            
                    # Clear context buffer after successfully processing a transaction line
                    recent_lines = []

    # Clean up any stray data processed before the first month was detected
    if "Unknown" in monthly_data_store and len(monthly_data_store) > 1:
        del monthly_data_store["Unknown"]

    num_active_months = max(1, len(monthly_data_store))

    # Compile Final Chart Data
    chart_data = []
    total_true_revenue = 0.0
    
    for month, data in monthly_data_store.items():
        chart_data.append({
            "Month": month,
            "True Revenue ($)": data["True Revenue"],
            "NSF Fees": data["NSF Count"]
        })
        total_true_revenue += data["True Revenue"]

    df_breakdown = pd.DataFrame(chart_data)
    auto_monthly_revenue = total_true_revenue / num_active_months
    avg_nsf_per_month = total_nsf_count / num_active_months

    # Process MCA Positions (Determine Frequency via Math)
    for lender, data in mca_tracker.items():
        avg_debits_per_month = data["debit_count"] / num_active_months
        
        # If they debit roughly 20-22 times a month, it's Daily. If 4-5, it's Weekly.
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

    # Display Month-by-Month Statement Breakdown Table
    st.markdown("### 📊 Extracted Financial Chart")
    st.dataframe(
        df_breakdown.style.format({
            "True Revenue ($)": "${:,.2f}"
        }), 
        use_container_width=True, hide_index=True
    )

    # Display Summary Headers
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Months Detected", f"{num_active_months} Months")
    m2.metric("Avg True Monthly Revenue", f"${auto_monthly_revenue:,.2f}")
    m3.metric("NSF Fees (Total / Avg)", f"{total_nsf_count} Total", f"{avg_nsf_per_month:.1f} / mo", delta_color="inverse" if total_nsf_count > 0 else "normal")
    m4.metric("Detected MCA Positions", f"{len(detected_funder_positions)} Funder(s)")

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

    if "positions" not in st.session_state:
        st.session_state.positions = [{"name": "Existing Funder #1", "amount": 150.0, "freq": "Daily"}]

    if detected_funder_positions and (len(st.session_state.positions) == 1 and st.session_state.positions[0]["name"] == "Existing Funder #1"):
        st.session_state.positions = []
        for funder in detected_funder_positions:
            st.session_state.positions.append({
                "name": funder["name"],
                "amount": funder["amount"],
                "freq": funder["freq"]
            })

    total_existing_monthly_debt = 0.0
    num_positions = len(st.session_state.positions)

    for i, pos in enumerate(st.session_state.positions):
        st.markdown(f"**Position #{i+1}**")
        c1, c2, c3, c4 = st.columns([2.5, 2, 2, 1])
        
        with c1:
            st.session_state.positions[i]["name"] = st.text_input(
                f"Lender Name #{i+1}", 
                value=pos.get("name", f"Position #{i+1}"), 
                key=f"name_{i}"
            )
        with c2:
            st.session_state.positions[i]["amount"] = st.number_input(
                f"Payment Amount ($) #{i+1}", 
                min_value=0.0, 
                value=float(pos["amount"]), 
                step=25.0, 
                key=f"amt_{i}"
            )
        with c3:
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

        with c4:
            st.write("")
            if st.button("🗑️", key=f"del_{i}"):
                st.session_state.positions.pop(i)
                st.rerun()

        st.caption(f"Monthly Impact: **${pos_monthly:,.2f}/mo** | **{pos_dsr_pct:.1f}% DSR**")
        st.write("---")

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

# --- SECTION 3: UNDERWRITING ENGINE & DECISION ---
existing_dsr = (total_existing_monthly_debt / avg_monthly_rev) if avg_monthly_rev > 0 else 0.0

risk_reasons = []
risk_multiplier = 1.0

# NSF Penalty Audit based on average per month
if 'avg_nsf_per_month' in locals():
    if avg_nsf_per_month > 3.0:
        risk_multiplier *= 0.70
        risk_reasons.append(f"NSF Fee Risk: High ({total_nsf_count} total, {avg_nsf_per_month:.1f}/mo — 30% penalty)")
    elif avg_nsf_per_month > 1.0:
        risk_multiplier *= 0.85
        risk_reasons.append(f"NSF Fee Risk: Moderate ({total_nsf_count} total, {avg_nsf_per_month:.1f}/mo — 15% penalty)")
    else:
        risk_reasons.append(f"NSF Fee Risk: Clean Record ({total_nsf_count} total fees, {avg_nsf_per_month:.1f}/mo — No penalty)")

# Credit Score Audit
if credit_score < 580:
    risk_multiplier *= 0.65
    risk_reasons.append(f"Credit Score: {credit_score} (Sub-580 FICO — 35% penalty)")
elif credit_score < 650:
    risk_multiplier *= 0.85
    risk_reasons.append(f"Credit Score: {credit_score} (Moderate FICO — 15% penalty)")
else:
    risk_reasons.append(f"Credit Score: {credit_score} (Prime FICO — No penalty)")

# Time in Business Audit
if tib_months < 12:
    risk_multiplier *= 0.70
    risk_reasons.append(f"Time in Business: {tib_months}m (<1 Year — 30% penalty)")
elif tib_months < 24:
    risk_multiplier *= 0.85
    risk_reasons.append(f"Time in Business: {tib_months}m (<2 Years — 15% penalty)")
else:
    risk_reasons.append(f"Time in Business: {tib_months}m (>2 Years — No penalty)")

# Industry Audit
if "High Risk" in industry_type:
    risk_multiplier *= 0.80
    risk_reasons.append("Industry: High Risk Sector — 20% penalty")
else:
    risk_reasons.append("Industry: Standard Risk Sector — No penalty")

# Bankruptcy Audit
if has_bk_collections:
    risk_reasons.append("Bankruptcy / Collections: ACTIVE ON RECORD (Hard Decline)")
else:
    risk_reasons.append("Bankruptcy / Collections: Clean Record")

# Stacking Position Penalties
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
