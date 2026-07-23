import streamlit as st
import pdfplumber
import pandas as pd
import re

st.set_page_config(page_title="MCA Bank Parser & Underwriting Tool", page_icon="💳", layout="wide")

st.title("💳 MCA Statement Analyzer & Underwriting Engine")
st.caption("Upload 6–10 bank statement PDFs to extract monthly breakdowns, true revenue, NSF fee occurrences, and lender positions.")

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

# --- PARSING FILTERS ---
VALID_REVENUE_KEYWORDS = [
    "DEPOSIT", "CREDIT", "ACH IN", "SQUARE", "STRIPE", "CLOVER", 
    "E-TRANSFER", "ETRANSFER", "INTERAC", "EMAIL TRSF", "ELECTRONIC TRANSFER IN", "WIRE IN"
]

EXCLUDED_KEYWORDS = [
    "INTERNAL TRANSFER", "TRANSFER FROM", "TRSF FROM", "MEMO TRANSFER", 
    "ACCOUNT TRANSFER", "TRANSFER BETWEEN", "TO SAVINGS", "FROM SAVINGS",
    "REFUND", "LOAN PROCEEDS", "LINE OF CREDIT", "LOC DRAW", "CASH ADVANCE PROCEEDS",
    "TOTAL DEPOSITS", "TOTAL CREDITS", "TOTAL DEBITS", "BEGINNING BALANCE", 
    "ENDING BALANCE", "SUMMARY", "AVERAGE BALANCE", "BALANCE FORWARD"
]

# Keywords specifically matching explicit NSF / Overdraft Fee Charge Lines
NSF_FEE_KEYWORDS = [
    "NSF FEE", "NSF CHARGE", "NON-SUFFICIENT FEE", "OVERDRAFT FEE", 
    "OVERDRAFT CHARGE", "RETURNED ITEM FEE", "RETURN ITEM FEE", "NSF RETURN"
]

# --- SECTION 1: BANK STATEMENT PDF UPLOADER ---
st.subheader("1. Bank Statement Ingestion & Month-by-Month Analysis")

uploaded_files = st.file_uploader(
    "Upload 6-10 Monthly Bank Statements (PDF format)", 
    type=["pdf"], 
    accept_multiple_files=True
)

auto_monthly_revenue = 0.0
avg_gross_deposits = 0.0
total_nsf_count = 0
avg_nsf_per_month = 0.0
detected_funder_positions = []

if uploaded_files:
    num_statements = len(uploaded_files)
    
    monthly_breakdown = []
    funder_totals = {}

    for pdf_file in uploaded_files:
        stmt_gross_deposits = 0.0
        stmt_withdrawals = 0.0
        stmt_true_revenue = 0.0
        stmt_nsf_fees = 0
        
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                
                lines = text.split("\n")
                for line in lines:
                    line_upper = line.upper()
                    
                    # 1. Explicit NSF Fee Occurrence Counting
                    if any(nsf_kw in line_upper for nsf_kw in NSF_FEE_KEYWORDS):
                        stmt_nsf_fees += 1

                    # Skip summary lines to prevent balance/revenue overestimation
                    if any(excl in line_upper for excl in EXCLUDED_KEYWORDS):
                        continue

                    # Extract numerical dollar amounts
                    amounts = re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2}", line)
                    if not amounts:
                        continue
                    
                    amount_val = float(amounts[-1].replace(",", ""))

                    # 2. Deposit vs. Withdrawal Categorization
                    if any(term in line_upper for term in VALID_REVENUE_KEYWORDS):
                        stmt_gross_deposits += amount_val
                        stmt_true_revenue += amount_val
                    elif any(w_kw in line_upper for w_kw in ["DEBIT", "WITHDRAWAL", "ACH OUT", "FEE", "PAYMENT", "PRE"]):
                        stmt_withdrawals += amount_val

                    # 3. Lender Position Detection & Frequency Categorization
                    for lender_name, meta in KNOWN_FUNDERS.items():
                        if any(kw in line_upper for kw in meta["keywords"]):
                            if "restrict_freq" in meta and "MONTHLY" in line_upper:
                                continue
                            
                            freq = "Weekly" if "WEEKLY" in line_upper else "Daily"
                            
                            if lender_name not in funder_totals:
                                funder_totals[lender_name] = {"total_paid": 0.0, "freq": freq, "occurrences": 0}
                            
                            funder_totals[lender_name]["total_paid"] += amount_val
                            funder_totals[lender_name]["occurrences"] += 1

        total_nsf_count += stmt_nsf_fees

        monthly_breakdown.append({
            "Statement / File": pdf_file.name,
            "Gross Deposits ($)": stmt_gross_deposits,
            "Withdrawals ($)": stmt_withdrawals,
            "True Revenue ($)": stmt_true_revenue,
            "NSF Fees Count": stmt_nsf_fees
        })

    # Summary Metrics Math
    df_breakdown = pd.DataFrame(monthly_breakdown)
    avg_gross_deposits = df_breakdown["Gross Deposits ($)"].mean() if not df_breakdown.empty else 0.0
    auto_monthly_revenue = df_breakdown["True Revenue ($)"].mean() if not df_breakdown.empty else 0.0
    avg_nsf_per_month = total_nsf_count / num_statements if num_statements > 0 else 0.0

    # Calculate average monthly position impact per lender
    for lender, data in funder_totals.items():
        avg_monthly_impact = data["total_paid"] / num_statements
        payment_amount = avg_monthly_impact / 21.67 if data["freq"] == "Daily" else avg_monthly_impact / 4.33
        
        detected_funder_positions.append({
            "name": lender,
            "amount": round(payment_amount, 2),
            "freq": data["freq"],
            "monthly_avg": round(avg_monthly_impact, 2)
        })

    # Display Month-by-Month Statement Breakdown Table (Including NSF Fee Column)
    st.markdown("### 📊 Statement-by-Statement Breakdown")
    st.dataframe(
        df_breakdown.style.format({
            "Gross Deposits ($)": "${:,.2f}",
            "Withdrawals ($)": "${:,.2f}",
            "True Revenue ($)": "${:,.2f}",
            "NSF Fees Count": "{:,.0f}"
        }), 
        use_container_width=True
    )

    # Display Summary Headers
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Avg Gross Deposits", f"${avg_gross_deposits:,.2f}")
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
    st.caption("Auto-calculated average daily/weekly payments per lender across uploaded statements.")

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
if avg_nsf_per_month > 3.0:
    risk_multiplier *= 0.70
    risk_reasons.append(f"NSF Fee Risk: High ({total_nsf_count} total, {avg_nsf_per_month:.1f}/mo — 30% penalty applied)")
elif avg_nsf_per_month > 1.0:
    risk_multiplier *= 0.85
    risk_reasons.append(f"NSF Fee Risk: Moderate ({total_nsf_count} total, {avg_nsf_per_month:.1f}/mo — 15% penalty applied)")
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
- Avg Gross Deposits: ${avg_gross_deposits:,.2f}
- Avg True Monthly Revenue: ${avg_monthly_rev:,.2f}
- NSF Fee Occurrences: {total_nsf_count} Total ({avg_nsf_per_month:.1f}/mo avg)
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
