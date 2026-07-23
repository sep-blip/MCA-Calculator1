import streamlit as st
import pandas as pd
import re
import pdfplumber
import io
from collections import defaultdict

st.set_page_config(page_title="MCA Bank Parser & Underwriting Tool", page_icon="💳", layout="wide")

st.title("💳 MCA Universal Bank Parser & Underwriting Engine")
st.caption("Upload bank statements (RBC, Scotiabank, CIBC, TD, BMO, Chase, BofA). Extracts multi-line transactions, isolates true revenue from internal transfers, detects competitor MCA debits, and calculates true MCA DSR.")
st.divider()

# --- LENDER DICTIONARY WITH ALIASES & TIERS ---
# STRICT KNOWN FUNDERS ONLY (No conventional bank loans or consumer lenders)
KNOWN_FUNDERS = {
    "Merchant Growth": {"tier": "Premium", "keywords": ["MERCHPAD", "MERCH PAD", "MERCHANT GROWTH"]},
    "Greenbox": {"tier": "Premium", "keywords": ["GREENBOX", "GREEN BOX", "GREENBOX CAPITAL"]},
    "Vault": {"tier": "Premium", "keywords": ["VAULT", "VAULT FINANCIAL"]},
    "Driven": {"tier": "Premium", "keywords": ["DRIVEN", "DRIVEN CAPITAL", "DRIVEN FINANCIAL"]},
    "Journey / OnDeck": {"tier": "Premium", "keywords": ["JOURNEY CAPITAL", "JOURNEY/ONDECK", "JOURNEY FUNDING", "ONDECK"]},
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
    "Business Credit Capital": {"tier": "Standard", "keywords": ["BCC EFT", "BUSINESS CR", "BCC", "BUSINESS CREDIT CAPITAL"]},
    "Flex Capital Group": {"tier": "Standard", "keywords": ["FLEXCAPITALGROUP", "FLEX CAPITAL", "FLEX CAPITAL GROUP"]},
    "ONTAP Capital": {"tier": "Standard", "keywords": ["ONTAP", "ONTAP CAPITAL", "ON TAP CAPITAL"]},
    "Clara Capital": {"tier": "Standard", "keywords": ["CLARA CAPITAL", "CLARA"]},
    "FUNDFI": {"tier": "Standard", "keywords": ["FUNDFI", "FUND FI", "FUND-FI"]},
    "TFG Financial": {"tier": "Standard", "keywords": ["TFG FINANCIAL", "TFG FINANCIAL CORPORATION"]}
}

REVENUE_EXCLUSIONS = [
    "INTERNAL TRANSFER", "TRANSFER FROM", "TRSF FROM", "MEMO TRANSFER", "ACCOUNT TRANSFER", 
    "MB-TRANSFER", "BR TO BR", "ONLINE BANKING TRANSFER", "IN-BRANCH TRANSFER", "INTERNET TRANSFER", 
    "LOAN", "BDC HASCAP", "LINE OF CREDIT", "LOC DRAW", "CASH ADVANCE", "ADVANCE PROCEEDS", 
    "REVERSAL", "REFUND", "RETURNED ITEM", "RTN WIRE", "PAYROLL", "ERROR CORRECTION", 
    "EXPIRED INTERAC", "RECLAIM", "CREDIT MEMO", "PRIVATE WEALTH", "CARAVEL", "UNITED TRADING"
]

# --- CACHED UNIVERSAL PDF PARSING ENGINE ---
@st.cache_data(show_spinner="📄 Extracting financial data from bank statements...")
def parse_uploaded_pdfs(files_data):
    monthly_store = defaultdict(lambda: {
        "Start Balance": 0.0, "Stated Credits": 0.0, "Non-Revenue": 0.0, 
        "True Revenue": 0.0, "Stated Debits": 0.0, "MCA Debits": 0.0, 
        "End Balance": 0.0, "NSF Count": 0
    })
    mca_store = defaultdict(lambda: {"total_amount": 0.0, "debit_count": 0})
    warnings = []

    for file_name, file_bytes in files_data:
        try:
            pdf_stream = io.BytesIO(file_bytes)
            
            with pdfplumber.open(pdf_stream) as pdf:
                full_pdf_text = ""
                for page in pdf.pages:
                    full_pdf_text += (page.extract_text() or "") + "\n"

            if len(full_pdf_text.strip()) < 50:
                warnings.append(f"**{file_name}** appears to be an image/scanned PDF without text layers. Please upload native text PDFs.")
                continue

            full_pdf_upper = full_pdf_text.upper()

            # Guardrail: Skip Non-Bank Documents
            if "EQUIFAX" in full_pdf_upper or "CREDIT PORTFOLIO INSIGHTS" in full_pdf_upper:
                warnings.append(f"Skipped non-bank statement: **{file_name}** (Credit Report Detected)")
                continue

            is_rbc = "ROYAL BANK OF CANADA" in full_pdf_upper or "RBC" in full_pdf_upper
            is_scotia = "SCOTIABANK" in full_pdf_upper or "BANK OF NOVA SCOTIA" in full_pdf_upper
            is_cibc = "CIBC" in full_pdf_upper or "CANADIAN IMPERIAL BANK" in full_pdf_upper

            # Month Extraction
            month_label = "Unknown Month"
            try:
                if is_cibc:
                    period_match = re.search(r"For\s+([A-Za-z]{3}\s+\d{1,2}\s+to\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4})", full_pdf_text)
                    if period_match:
                        end_date_str = period_match.group(1).split("to")[-1].strip()
                        month_label = pd.to_datetime(end_date_str).strftime("%b %Y")
                elif is_rbc:
                    period_match = re.search(r"([A-Za-z]+\s+\d{1,2},\s+\d{4})\s+to\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", full_pdf_text)
                    if period_match:
                        month_label = pd.to_datetime(period_match.group(2)).strftime("%b %Y")
                elif is_scotia:
                    period_match = re.search(r"(\b[A-Za-z]{3}\s+\d{1,2}\s+\d{4}\b)\s+(\b[A-Za-z]{3}\s+\d{1,2}\s+\d{4}\b)", full_pdf_text)
                    if period_match:
                        month_label = pd.to_datetime(period_match.group(2)).strftime("%b %Y")
                
                if month_label == "Unknown Month":
                    date_matches = re.findall(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+(\d{4})\b", full_pdf_text, re.IGNORECASE)
                    if date_matches:
                        month_label = f"{date_matches[0][0].capitalize()} {date_matches[0][1]}"
            except Exception:
                month_label = file_name[:15]

            start_bal, stated_credits, stated_debits, end_bal = 0.0, 0.0, 0.0, 0.0

            # --------------------------------------------------
            # 1. SUMMARY BOX PARSER FOR STATED TOTALS
            # --------------------------------------------------
            if is_cibc:
                open_m = re.search(r"Opening\s+balance\s+on\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)
                deb_m = re.search(r"Withdrawals[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)
                cred_m = re.search(r"Deposits[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)
                close_m = re.search(r"Closing\s+balance\s+on\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)

                if open_m: start_bal = float(open_m.group(1).replace(",", ""))
                if cred_m: stated_credits = float(cred_m.group(1).replace(",", ""))
                if deb_m: stated_debits = float(deb_m.group(1).replace(",", ""))
                if close_m: end_bal = float(close_m.group(1).replace(",", ""))

            elif is_rbc:
                open_m = re.search(r"Opening\s+balance[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)
                cred_m = re.search(r"Total\s+deposits\s*&\s*credits\s*\(\d+\)[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)
                deb_m = re.search(r"Total\s+cheques\s*&\s*debits\s*\(\d+\)[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)
                close_m = re.search(r"Closing\s+balance[^\d]*?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)

                if open_m: start_bal = float(open_m.group(1).replace(",", ""))
                if cred_m: stated_credits = float(cred_m.group(1).replace(",", ""))
                if deb_m: stated_debits = float(deb_m.group(1).replace(",", ""))
                if close_m: end_bal = float(close_m.group(1).replace(",", ""))

            elif is_scotia:
                sum_m = re.search(r"Account\s+Summary\s+for\s+this\s+Period:[\s\S]*?(\d+)\s+\$?([\d,]+\.\d{2})\s+(\d+)\s+\$?([\d,]+\.\d{2})", full_pdf_text, re.IGNORECASE)
                if sum_m:
                    stated_debits = float(sum_m.group(2).replace(",", ""))
                    stated_credits = float(sum_m.group(4).replace(",", ""))
                
                bal_match = re.search(r"BALANCE\s+FORWARD\s+([\d,]+\.\d{2})", full_pdf_upper)
                if bal_match: start_bal = float(bal_match.group(1).replace(",", ""))
                
                all_amounts = re.findall(r"\b\d[\d,]*\.\d{2}\b", full_pdf_text)
                if all_amounts: end_bal = float(all_amounts[-1].replace(",", ""))

            # --------------------------------------------------
            # 2. MULTI-LINE TRANSACTION BLOCK ANALYSIS
            # --------------------------------------------------
            lines = [l.strip() for l in full_pdf_text.split("\n") if l.strip()]
            non_revenue_credits = 0.0
            file_mca_debits = 0.0
            file_nsf_count = 0

            running_bal = start_bal
            current_block = []

            for line in lines:
                u = line.upper()

                # Skip Header/Footer Lines
                if any(ignore_term in u for ignore_term in ["BALANCE FORWARD", "OPENING BALANCE", "CLOSING BALANCE", "ACCOUNT SUMMARY", "PAGE ", "IMPORTANT:"]):
                    continue

                # NSF Fee Count (>= $20.00)
                if any(kw in u for kw in ["NSF ITEM FEE", "OVERDRAWN HANDLING CHGS", "NSF FEE", "NON-SUFFICIENT", "RETURNED ITEM FEE"]):
                    multi_match = re.search(r"(\d+)\s*@\s*\$?(\d+\.\d{2})", u)
                    if multi_match:
                        cnt = int(multi_match.group(1))
                        fee_val = float(multi_match.group(2))
                        if fee_val >= 20.0: file_nsf_count += cnt
                    else:
                        amts = [float(a.replace(",", "")) for a in re.findall(r"\b\d[\d,]*\.\d{2}\b", u)]
                        if amts and amts[0] >= 20.0: file_nsf_count += 1
                        elif not amts: file_nsf_count += 1

                current_block.append(u)

                # Balance Math Proof: Check if line ends with Transaction Amount & Ending Balance
                amts = [float(a.replace(",", "")) for a in re.findall(r"\b\d[\d,]*\.\d{2}\b", line)]
                if len(amts) >= 2:
                    bal = amts[-1]
                    tx_amt = amts[-2]

                    block_str = " ".join(current_block)
                    diff = round(bal - running_bal, 2)

                    if abs(diff - tx_amt) < 0.05:
                        # Verified INCOMING CREDIT / DEPOSIT
                        if any(kw in block_str for kw in REVENUE_EXCLUSIONS):
                            non_revenue_credits += tx_amt
                        running_bal = bal
                        current_block = []

                    elif abs(diff - (-tx_amt)) < 0.05:
                        # Verified OUTGOING DEBIT / WITHDRAWAL (Strict KNOWN_FUNDERS Match)
                        for lender_name, meta in KNOWN_FUNDERS.items():
                            if any(kw in block_str for kw in meta["keywords"]):
                                if "25,000.00" in block_str and "JOURNEY" in block_str:
                                    continue
                                file_mca_debits += tx_amt
                                mca_store[lender_name]["total_amount"] += tx_amt
                                mca_store[lender_name]["debit_count"] += 1
                                break
                        running_bal = bal
                        current_block = []

            true_revenue = max(0.0, stated_credits - non_revenue_credits)

            # Store Monthly Data
            monthly_store[month_label]["Start Balance"] = start_bal
            monthly_store[month_label]["Stated Credits"] += stated_credits
            monthly_store[month_label]["Non-Revenue"] += non_revenue_credits
            monthly_store[month_label]["True Revenue"] += true_revenue
            monthly_store[month_label]["Stated Debits"] += stated_debits
            monthly_store[month_label]["MCA Debits"] += file_mca_debits
            monthly_store[month_label]["End Balance"] = end_bal
            monthly_store[month_label]["NSF Count"] += file_nsf_count

        except Exception as e:
            warnings.append(f"Error reading file **{file_name}**: {str(e)}")

    return dict(monthly_store), dict(mca_store), warnings

# --- SECTION 1: BANK STATEMENT UPLOADER ---
st.subheader("1. Bank Statement Ingestion & Month-by-Month Analysis")

uploaded_files = st.file_uploader(
    "Upload Bank Statements (PDFs)", 
    type=["pdf"], 
    accept_multiple_files=True
)

auto_monthly_revenue = 0.0
total_nsf_count = 0
detected_funder_positions = []

if uploaded_files:
    files_payload = [(f.name, f.getvalue()) for f in uploaded_files]
    monthly_data_store, mca_tracker, warnings = parse_uploaded_pdfs(files_payload)

    for w in warnings:
        st.warning(f"⚠️ {w}")

    if monthly_data_store:
        num_active_months = max(1, len(monthly_data_store))
        chart_data = []
        total_true_revenue = 0.0
        total_stated_credits = 0.0
        total_stated_debits = 0.0
        total_mca_debits = 0.0

        for month, data in monthly_data_store.items():
            true_rev = data["True Revenue"]
            mca_debits = data["MCA Debits"]
            
            # MCA Debt % = Known Competitor MCA Debits / True Revenue
            mca_debt_pct = (mca_debits / true_rev * 100) if true_rev > 0 else 0.0

            chart_data.append({
                "Month": month,
                "Start Balance ($)": data["Start Balance"],
                "Stated Credits ($)": data["Stated Credits"],
                "Non-Rev Exclusions ($)": data["Non-Revenue"],
                "True Revenue ($)": true_rev,
                "Stated Debits ($)": data["Stated Debits"],
                "MCA Debits ($)": mca_debits,
                "MCA Debt %": mca_debt_pct,
                "NSF Fees (≥$20)": data["NSF Count"],
                "End Balance ($)": data["End Balance"]
            })
            total_true_revenue += true_rev
            total_stated_credits += data["Stated Credits"]
            total_stated_debits += data["Stated Debits"]
            total_mca_debits += mca_debits
            total_nsf_count += data["NSF Count"]

        df_breakdown = pd.DataFrame(chart_data)
        auto_monthly_revenue = total_true_revenue / num_active_months
        avg_monthly_credits = total_stated_credits / num_active_months
        avg_monthly_debits = total_stated_debits / num_active_months
        avg_mca_debits = total_mca_debits / num_active_months
        avg_nsf_per_month = total_nsf_count / num_active_months

        # Evaluate MCA Funder Frequency & Monthly Averages
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
                "Non-Rev Exclusions ($)": "${:,.2f}",
                "True Revenue ($)": "${:,.2f}",
                "Stated Debits ($)": "${:,.2f}",
                "MCA Debits ($)": "${:,.2f}",
                "MCA Debt %": "{:.1f}%",
                "End Balance ($)": "${:,.2f}",
                "NSF Fees (≥$20)": "{:,.0f}"
            }), 
            use_container_width=True, hide_index=True
        )

        st.markdown("### 📈 True Revenue vs. Competitor MCA Debits Comparison")
        if not df_breakdown.empty:
            chart_df = df_breakdown.set_index("Month")[["True Revenue ($)", "MCA Debits ($)"]]
            st.bar_chart(chart_df)

            st.markdown("#### 💡 Monthly MCA Competitor Debt Ratio Breakdown")
            m_cols = st.columns(min(len(df_breakdown), 6))
            for idx, row in df_breakdown.iterrows():
                with m_cols[idx % len(m_cols)]:
                    st.metric(
                        label=f"{row['Month']} MCA Debt %",
                        value=f"{row['MCA Debt %']:.1f}%",
                        delta=f"${row['MCA Debits ($)']:,.2f} MCA Debits",
                        delta_color="off"
                    )

        st.markdown("### 📌 Multi-Month Overview & Averages")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Active Months", f"{num_active_months} Month(s)")
        c2.metric("Avg Monthly Deposits", f"${avg_monthly_credits:,.2f}", f"${total_stated_credits:,.2f} Total")
        c3.metric("Avg True Monthly Rev", f"${auto_monthly_revenue:,.2f}", f"${total_true_revenue:,.2f} Total")
        c4.metric("Avg Competitor MCA Debt", f"${avg_mca_debits:,.2f} / mo", f"${total_mca_debits:,.2f} Total")
        c5.metric("NSF Fees (≥$20)", f"{total_nsf_count} Total", f"{avg_nsf_per_month:.1f} / mo", delta_color="inverse" if total_nsf_count > 0 else "normal")

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
            st.session_state.positions = []

    total_existing_monthly_debt = 0.0
    num_positions = len(st.session_state.positions)

    to_delete = None
    if st.session_state.positions:
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
    else:
        st.info("No active competitor MCA debt positions detected.")

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
        risk_reasons.append(f"NSF Fee Risk: High ({total_nsf_count} total ≥$20, {avg_nsf_per_month:.1f}/mo — 30% penalty)")
    elif avg_nsf_per_month > 1.0:
        risk_multiplier *= 0.85
        risk_reasons.append(f"NSF Fee Risk: Moderate ({total_nsf_count} total ≥$20, {avg_nsf_per_month:.1f}/mo — 15% penalty)")
    else:
        risk_reasons.append(f"NSF Fee Risk: Clean Record ({total_nsf_count} total fees ≥$20 — No penalty)")

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
    risk_reasons.append(f"Active Positions: {num_positions} Position(s) (Clean — No penalty)")

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
