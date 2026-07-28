import streamlit as st
import pandas as pd
import re
import pdfplumber
import io
from collections import defaultdict
from datetime import datetime

st.set_page_config(page_title="Forward Funding MCA Pricing Tool", page_icon="💳", layout="wide")

st.title("💳 Forward Funding Underwriting Engine")
st.caption("Upload bank statements (Canadian Business Accounts - English & French). Interactive transaction reclassification, month detection, and underwriting pricing calculations are fully integrated.")
st.divider()

# --- FRENCH & ENGLISH MONTH MAPPING ---
MONTH_MAP = {
    "JAN": "Jan", "JANVIER": "Jan", "FEB": "Feb", "FEV": "Feb", "FÉVRIER": "Feb", "FEVRIER": "Feb",
    "MAR": "Mar", "MARS": "Mar", "APR": "Apr", "AVR": "Apr", "AVRIL": "Apr", "MAY": "May", "MAI": "May",
    "JUN": "Jun", "JUIN": "Jun", "JUL": "Jul", "JUIL": "Jul", "JUILLET": "Jul", "AUG": "Aug", "AOU": "Aug", "AOÛT": "Aug",
    "SEP": "Sep", "SEPTEMBRE": "Sep", "OCT": "Oct", "OCTOBRE": "Oct", "NOV": "Nov", "NOVEMBRE": "Nov",
    "DEC": "Dec", "DECEMBRE": "Dec", "DÉCEMBRE": "Dec"
}

# --- LENDER DICTIONARY WITH ALIASES & TIERS ---
KNOWN_FUNDERS = {
    "Merchant Growth": {"tier": "Premium", "keywords": ["MERCHPAD", "MERCH PAD", "MERCHANT GROWTH"]},
    "Greenbox": {"tier": "Premium", "keywords": ["GREENBOX", "GREEN BOX", "GREENBOX CAPITAL"]},
    "Vault": {"tier": "Premium", "keywords": ["VAULT", "VAULT FINANCIAL"]},
    "Driven": {"tier": "Premium", "keywords": ["DRIVEN", "DRIVEN CAPITAL", "DRIVEN FINANCIAL"]},
    "Journey / OnDeck": {"tier": "Premium", "keywords": ["JOURNEY CAPITAL", "JOURNEY/ONDECK", "JOURNEY FUNDING", "ONDECK"]},
    "iCapital": {"tier": "Premium", "keywords": ["ICAPITAL", "I CAPITAL", "I-CAPITAL"]},
    "Canacap": {"tier": "Standard", "keywords": ["CANA CAP", "CANACAP", "CANA CAPITAL", "CANACAPITAL", "CCP", "ccp"]},
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
    "EXPIRED INTERAC", "RECLAIM", "CREDIT MEMO", "PRIVATE WEALTH", "CARAVEL", "UNITED TRADING",
    "ONLINE TRANSFER, TF", "TF 3219", "E-TRANSFER CANCELLED", "NSF FEE REV", "SERVICE CHARGE ADJUSTMENT",
    "VIREMENT ACCÈSD", "VIREMENT ACCESD", "369408 EOP", "AVANCE FONDS"
]

def clean_french_amount(text):
    """Normalizes French space-separated amounts like '10 000.00' into standard float numbers."""
    return re.sub(r"(\d)\s+(\d)", r"\1\2", text)

# --- CACHED UNIVERSAL PDF PARSING ENGINE WITH FRENCH SUPPORT ---
@st.cache_data(show_spinner="📄 Extracting financial data ...")
def parse_uploaded_pdfs(files_data):
    all_transactions = []
    month_summary_store = defaultdict(lambda: {
        "Start Balance": 0.0, "End Balance": 0.0, "Daily Balances": [], "Beginning Date": "N/A", "Ending Date": "N/A"
    })
    warnings = []

    for file_name, file_bytes in files_data:
        try:
            pdf_stream = io.BytesIO(file_bytes)
            
            with pdfplumber.open(pdf_stream) as pdf:
                full_pdf_text = "\n".join([page.extract_text() or "" for page in pdf.pages])

            if len(full_pdf_text.strip()) < 50:
                warnings.append(f"**{file_name}** appears to be an image/scanned PDF without text layers. Please upload native text PDFs.")
                continue

            full_pdf_text_clean = clean_french_amount(full_pdf_text)
            full_pdf_upper = full_pdf_text_clean.upper()

            if "EQUIFAX" in full_pdf_upper or "CREDIT PORTFOLIO INSIGHTS" in full_pdf_upper:
                warnings.append(f"Skipped non-bank statement: **{file_name}** (Credit Report Detected)")
                continue

            # 1. Month/Year & Period Extractor
            month_label = "Unknown Month"
            beg_date, end_date = "N/A", "N/A"
            try:
                desjardins_match = re.search(r"du\s+(\d{1,2})(?:er)?\s+([A-Za-zÉéû]+)\s+au\s+(\d{1,2})\s+([A-Za-zÉéû]+)\s+(\d{4})", full_pdf_text_clean, re.IGNORECASE)
                if desjardins_match:
                    raw_month = desjardins_match.group(4).upper()
                    year = desjardins_match.group(5)
                    std_month = MONTH_MAP.get(raw_month, "Unknown")
                    if std_month != "Unknown":
                        month_label = f"{std_month} {year}"
                    beg_date = f"{year}-{desjardins_match.group(1).zfill(2)}-01"
                    end_date = f"{year}-{desjardins_match.group(3).zfill(2)}-28"

                if month_label == "Unknown Month":
                    period_match_td = re.search(r"Statement From - To\s*\n?\s*([A-Za-z]{3}\s+\d{1,2}/\d{2})\s*-\s*([A-Za-z]{3}\s+\d{1,2}/\d{2})", full_pdf_text_clean, re.IGNORECASE)
                    if period_match_td:
                        end_str = period_match_td.group(2).strip()
                        dt_end = pd.to_datetime(end_str, format="%b %d/%y")
                        month_label = dt_end.strftime("%b %Y")
                        beg_date = pd.to_datetime(period_match_td.group(1).strip(), format="%b %d/%y").strftime("%Y-%m-%d")
                        end_date = dt_end.strftime("%Y-%m-%d")
                    else:
                        date_matches = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", full_pdf_text_clean)
                        if date_matches:
                            beg_date, end_date = date_matches[0], date_matches[-1]
                            month_label = pd.to_datetime(beg_date).strftime("%b %Y")
            except Exception:
                month_label = file_name[:15]

            # 2. Extract Balances
            start_bal, end_bal = 0.0, 0.0
            start_match = re.search(r"(?:Solde reporté|BALANCE FORWARD)[^\n]*?([-\d,]+\.\d{2})(?:OD)?", full_pdf_text_clean, re.IGNORECASE)
            if start_match:
                start_bal = float(start_match.group(1).replace(",", ""))

            bal_matches = re.findall(r"\b\d{1,3}(?:,\d{3})*\.\d{2}(?:OD)?\b", full_pdf_text_clean)
            if bal_matches:
                end_bal = float(bal_matches[-1].replace("OD", "").replace(",", ""))

            month_summary_store[month_label]["Start Balance"] = start_bal
            month_summary_store[month_label]["End Balance"] = end_bal
            month_summary_store[month_label]["Beginning Date"] = beg_date
            month_summary_store[month_label]["Ending Date"] = end_date

            # 3. Line-by-Line Transaction Analysis
            lines = [l.strip() for l in full_pdf_text_clean.split("\n") if l.strip()]

            for line in lines:
                u = line.upper()

                if any(term in u for term in ["SOLDE REPORTÉ", "BALANCE FORWARD", "OPENING BALANCE", "CLOSING BALANCE", "ACCOUNT SUMMARY", "PAGE ", "IMPORTANT:"]):
                    continue

                amts = re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2}", line)
                if not amts:
                    continue

                tx_amt = float(amts[0].replace(",", ""))

                if len(amts) >= 2:
                    month_summary_store[month_label]["Daily Balances"].append(float(amts[-1].replace(",", "")))

                is_credit = False
                if any(kw in u for kw in ["DÉPÔT", "DEPOT", "VIR. INTERAC DE", "VIREMENT INTERAC DE", "DÉPÔT DIRECT", "DEPOT DIRECT", "AVANCE FONDS", "RISTOURNE", "DEPOSIT", "CREDIT", "RECEIVED", "INCOMING"]) and not "SENT" in u:
                    is_credit = True
                elif any(code in u for code in ["VRW", "DI", "RIS"]):
                    is_credit = True

                tx_type = "Credit (Deposit)" if is_credit else "Debit (Withdrawal)"
                is_etransfer = any(kw in u for kw in ["E-TRANSFER", "E-TFR", "INTERAC", "VIR. INTERAC DE", "VRW"]) and is_credit
                category = "Operational Revenue" if tx_type == "Credit (Deposit)" else "Standard Operating Expense"

                for lender_name, meta in KNOWN_FUNDERS.items():
                    if any(kw in u for kw in meta["keywords"]):
                        if "DÉPÔT DIRECT" in u or "MERCHANT GROWTH INV" in u or is_credit:
                            continue
                        category = f"MCA Debt ({lender_name})"
                        break

                if any(kw in u for kw in ["NSF ITEM FEE", "RETURNED ITEM FEE", "NSF RETURN FEE", "OVERDRAWN HANDLING CHGS"]):
                    category = "NSF / Overdraft Fee"
                elif is_credit and not is_etransfer:
                    if any(ex in u for ex in REVENUE_EXCLUSIONS) or "WIRE" in u:
                        category = "Non-Revenue Exclusion"

                all_transactions.append({
                    "Month": month_label,
                    "Description": line[:80],
                    "Amount ($)": tx_amt,
                    "Transaction Type": tx_type,
                    "Category": category,
                    "Source File": file_name
                })

        except Exception as e:
            warnings.append(f"Error reading file **{file_name}**: {str(e)}")

    return dict(month_summary_store), pd.DataFrame(all_transactions), warnings

# --- SECTION 1: BANK STATEMENT UPLOADER ---
st.subheader("1. Bank Statement Ingestion & Month-by-Month Analysis")

uploaded_files = st.file_uploader("Upload Bank Statements (PDFs)", type=["pdf"], accept_multiple_files=True)

auto_monthly_revenue = 0.0
total_nsf_count = 0
detected_funder_positions = []

if uploaded_files:
    if "df_transactions" not in st.session_state or st.sidebar.button("🔄 Re-process Uploaded Files"):
        files_payload = [(f.name, f.getvalue()) for f in uploaded_files]
        month_summary_store, df_tx, warnings = parse_uploaded_pdfs(files_payload)
        st.session_state.month_summary_store = month_summary_store
        st.session_state.df_transactions = df_tx
        st.session_state.warnings = warnings

    df_tx = st.session_state.df_transactions
    month_summary_store = st.session_state.month_summary_store

    for w in st.session_state.get("warnings", []):
        st.warning(f"⚠️ {w}")

    if not df_tx.empty:
        # --- SECTION 2: INTERACTIVE TRANSACTION CLASSIFIER ---
        with st.expander("✏️ Interactive Transaction Classifier (Click to Expand)", expanded=False):
            st.caption("Review extracted transaction line items below. Edit classification categories to update underwriting totals dynamically.")

            edited_df = st.data_editor(
                df_tx,
                column_config={
                    "Category": st.column_config.SelectboxColumn(
                        "Classification Category",
                        options=[
                            "Operational Revenue",
                            "Non-Revenue Exclusion",
                            "Standard Operating Expense",
                            "MCA Debt (Merchant Growth)",
                            "MCA Debt (Greenbox)",
                            "MCA Debt (Vault)",
                            "MCA Debt (Driven)",
                            "MCA Debt (Journey / OnDeck)",
                            "MCA Debt (iCapital)",
                            "MCA Debt (Canacap)",
                            "MCA Debt (2M7)",
                            "MCA Debt (Other)",
                            "NSF / Overdraft Fee"
                        ],
                        required=True
                    ),
                    "Amount ($)": st.column_config.NumberColumn("Amount ($)", format="$%.2f"),
                    "Month": st.column_config.TextColumn("Statement Month")
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic"
            )
            st.session_state.df_transactions = edited_df

        # --- RE-CALCULATE MONTHLY FINANCIAL BREAKDOWN ---
        monthly_table = []
        mca_tracker = defaultdict(lambda: {"total_amount": 0.0, "debit_count": 0})

        for month in sorted(edited_df["Month"].unique()):
            m_tx = edited_df[edited_df["Month"] == month]
            m_info = month_summary_store.get(month, {"Start Balance": 0.0, "End Balance": 0.0, "Daily Balances": [], "Beginning Date": "N/A", "Ending Date": "N/A"})

            credits_df = m_tx[m_tx["Transaction Type"] == "Credit (Deposit)"]
            num_deposits = len(credits_df)
            total_deposits = credits_df["Amount ($)"].sum()

            rev_df = m_tx[m_tx["Category"] == "Operational Revenue"]
            num_revenue = len(rev_df)
            total_revenue = rev_df["Amount ($)"].sum()

            non_rev_df = m_tx[m_tx["Category"] == "Non-Revenue Exclusion"]
            num_non_rev = len(non_rev_df)
            total_non_rev = non_rev_df["Amount ($)"].sum()

            debits_df = m_tx[m_tx["Transaction Type"] == "Debit (Withdrawal)"]
            num_withdrawals = len(debits_df)
            total_withdrawals = debits_df["Amount ($)"].sum()

            mca_mask = m_tx["Category"].str.contains("MCA Debt", na=False)
            mca_df = m_tx[mca_mask]
            num_loan_debits = len(mca_df)
            total_loan_debits = mca_df["Amount ($)"].sum()

            for _, row in mca_df.iterrows():
                funder_name = row["Category"].replace("MCA Debt (", "").replace(")", "")
                mca_tracker[funder_name]["total_amount"] += row["Amount ($)"]
                mca_tracker[funder_name]["debit_count"] += 1

            nsf_count = len(m_tx[m_tx["Category"] == "NSF / Overdraft Fee"])
            daily_bals = m_info.get("Daily Balances", [])
            avg_bal = sum(daily_bals) / len(daily_bals) if daily_bals else m_info["End Balance"]
            neg_days = sum(1 for b in daily_bals if b < 0)

            monthly_table.append({
                "Month": month,
                "Beginning Date": m_info["Beginning Date"],
                "Ending Date": m_info["Ending Date"],
                "Starting Balance": m_info["Start Balance"],
                "# Deposits": num_deposits,
                "Total Deposits": total_deposits,
                "# Revenue": num_revenue,
                "Total Revenue (Excluding Transfers)": total_revenue,
                "# Non Revenue": num_non_rev,
                "Total Non Revenue": total_non_rev,
                "# Withdrawals": num_withdrawals,
                "Total Withdrawals": total_withdrawals,
                "# Loan Debits": num_loan_debits,
                "Loan Debits": total_loan_debits,
                "Ending Balance": m_info["End Balance"],
                "Average Balance": avg_bal,
                "# Negative Balance Days": neg_days,
                "# NSF": nsf_count
            })

        df_summary = pd.DataFrame(monthly_table)
        num_active_months = max(1, len(df_summary))

        auto_monthly_revenue = df_summary["Total Revenue (Excluding Transfers)"].sum() / num_active_months
        total_nsf_count = df_summary["# NSF"].sum()

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

        # BUILD TOTAL & AVERAGE ROWS
        total_row = {
            "Month": "TOTAL", "Beginning Date": "", "Ending Date": "",
            "Starting Balance": df_summary["Starting Balance"].sum(),
            "# Deposits": df_summary["# Deposits"].sum(),
            "Total Deposits": df_summary["Total Deposits"].sum(),
            "# Revenue": df_summary["# Revenue"].sum(),
            "Total Revenue (Excluding Transfers)": df_summary["Total Revenue (Excluding Transfers)"].sum(),
            "# Non Revenue": df_summary["# Non Revenue"].sum(),
            "Total Non Revenue": df_summary["Total Non Revenue"].sum(),
            "# Withdrawals": df_summary["# Withdrawals"].sum(),
            "Total Withdrawals": df_summary["Total Withdrawals"].sum(),
            "# Loan Debits": df_summary["# Loan Debits"].sum(),
            "Loan Debits": df_summary["Loan Debits"].sum(),
            "Ending Balance": df_summary["Ending Balance"].sum(),
            "Average Balance": df_summary["Average Balance"].sum(),
            "# Negative Balance Days": df_summary["# Negative Balance Days"].sum(),
            "# NSF": df_summary["# NSF"].sum()
        }

        avg_row = {
            "Month": "Average", "Beginning Date": "", "Ending Date": "",
            "Starting Balance": df_summary["Starting Balance"].mean(),
            "# Deposits": df_summary["# Deposits"].mean(),
            "Total Deposits": df_summary["Total Deposits"].mean(),
            "# Revenue": df_summary["# Revenue"].mean(),
            "Total Revenue (Excluding Transfers)": df_summary["Total Revenue (Excluding Transfers)"].mean(),
            "# Non Revenue": df_summary["# Non Revenue"].mean(),
            "Total Non Revenue": df_summary["Total Non Revenue"].mean(),
            "# Withdrawals": df_summary["# Withdrawals"].mean(),
            "Total Withdrawals": df_summary["Total Withdrawals"].mean(),
            "# Loan Debits": df_summary["# Loan Debits"].mean(),
            "Loan Debits": df_summary["Loan Debits"].mean(),
            "Ending Balance": df_summary["Ending Balance"].mean(),
            "Average Balance": df_summary["Average Balance"].mean(),
            "# Negative Balance Days": df_summary["# Negative Balance Days"].mean(),
            "# NSF": df_summary["# NSF"].mean()
        }

        df_display = pd.concat([df_summary, pd.DataFrame([total_row, avg_row])], ignore_index=True)

        # TABBED VIEW IMPLEMENTATION (MATCHING SCREENSHOT)
        tab_bank, tab_debt, tab_rev, tab_non_rev, tab_flags = st.tabs([
            "Bank Statement Summary", "Debt Summary", "Revenue Summary", "Non-Revenue Summary", "Flags"
        ])

        with tab_bank:
            st.markdown("### Bank Statement Aggregation Summary")
            
            def highlight_totals(s):
                if s["Month"] in ["TOTAL", "Average"]:
                    return ['color: red; font-weight: bold;'] * len(s)
                return [''] * len(s)

            formatted_df = df_display.style.apply(highlight_totals, axis=1).format({
                "Starting Balance": "${:,.2f}",
                "# Deposits": "{:,.2f}",
                "Total Deposits": "${:,.2f}",
                "# Revenue": "{:,.2f}",
                "Total Revenue (Excluding Transfers)": "${:,.2f}",
                "# Non Revenue": "{:,.2f}",
                "Total Non Revenue": "${:,.2f}",
                "# Withdrawals": "{:,.2f}",
                "Total Withdrawals": "${:,.2f}",
                "# Loan Debits": "{:,.2f}",
                "Loan Debits": "${:,.2f}",
                "Ending Balance": "${:,.2f}",
                "Average Balance": "${:,.2f}",
                "# Negative Balance Days": "{:,.2f}",
                "# NSF": "{:,.2f}"
            })
            st.dataframe(formatted_df, use_container_width=True, hide_index=True)

        with tab_debt:
            st.markdown("### 💳 Classified Debt & MCA Loan Debits Bucket")
            debt_tx = edited_df[edited_df["Category"].str.contains("MCA Debt", na=False)]
            m_filter = st.multiselect("Filter by Month:", options=edited_df["Month"].unique(), key="debt_m_filter")
            if m_filter:
                debt_tx = debt_tx[debt_tx["Month"].isin(m_filter)]
            st.dataframe(debt_tx.style.format({"Amount ($)": "${:,.2f}"}), use_container_width=True, hide_index=True)

        with tab_rev:
            st.markdown("### 🟢 Operational Revenue Bucket")
            rev_tx = edited_df[edited_df["Category"] == "Operational Revenue"]
            m_filter_r = st.multiselect("Filter by Month:", options=edited_df["Month"].unique(), key="rev_m_filter")
            if m_filter_r:
                rev_tx = rev_tx[rev_tx["Month"].isin(m_filter_r)]
            st.dataframe(rev_tx.style.format({"Amount ($)": "${:,.2f}"}), use_container_width=True, hide_index=True)

        with tab_non_rev:
            st.markdown("### 🛑 Non-Revenue Exclusions Bucket")
            non_rev_tx = edited_df[edited_df["Category"] == "Non-Revenue Exclusion"]
            m_filter_nr = st.multiselect("Filter by Month:", options=edited_df["Month"].unique(), key="non_rev_m_filter")
            if m_filter_nr:
                non_rev_tx = non_rev_tx[non_rev_tx["Month"].isin(m_filter_nr)]
            st.dataframe(non_rev_tx.style.format({"Amount ($)": "${:,.2f}"}), use_container_width=True, hide_index=True)

        with tab_flags:
            st.markdown("### ⚠️ NSF & Negative Balance Flags")
            flag_tx = edited_df[edited_df["Category"] == "NSF / Overdraft Fee"]
            st.dataframe(flag_tx.style.format({"Amount ($)": "${:,.2f}"}), use_container_width=True, hide_index=True)

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
    st.caption("Funder, frequency, and payment calculated from statement history.")

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

avg_nsf_per_month = (total_nsf_count / num_active_months) if 'num_active_months' in locals() else 0.0

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
