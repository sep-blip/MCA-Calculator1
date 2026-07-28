import streamlit as st
import pandas as pd
import re
import pdfplumber
import io
from collections import defaultdict
from datetime import datetime

st.set_page_config(page_title="Forward Funding MCA Pricing Tool", page_icon="💳", layout="wide")

st.title("💳 Forward Funding Universal Underwriting Engine")
st.caption("Universal Canadian Bank Statement Parser. Utilizes visual column reconstruction, delta-math classification, and automated end-balance reconciliation.")
st.divider()

# --- DICTIONARIES & EXCLUSIONS ---
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

FR_EN_MONTHS = {
    "JANVIER": "JAN", "JANV": "JAN", "FÉVRIER": "FEB", "FEVRIER": "FEB", "FEV": "FEB",
    "MARS": "MAR", "AVRIL": "APR", "AVR": "APR", "MAI": "MAY", "JUIN": "JUN", 
    "JUILLET": "JUL", "JUIL": "JUL", "AOÛT": "AUG", "AOUT": "AUG", "SEPTEMBRE": "SEP", 
    "SEPT": "SEP", "OCTOBRE": "OCT", "NOVEMBRE": "NOV", "DÉCEMBRE": "DEC", "DECEMBRE": "DEC"
}
MONTHS_ALL = sorted(list(set(list(FR_EN_MONTHS.keys()) + ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])), key=len, reverse=True)
MONTHS_REGEX = r"(?:" + "|".join(MONTHS_ALL) + r")"
DATE_PATTERN = rf"\b(?:(\d{{1,2}})\s*({MONTHS_REGEX})|({MONTHS_REGEX})\s*(\d{{1,2}}))\b"
AMOUNT_PATTERN = r"(?<!\d)(?:-?\d{1,3}(?:[ \xA0,]\d{3})+|-?\d+)[.,]\d{2}(?!\d)"

def parse_amount(amt_str):
    """Safely converts English/French mixed formatting into a float."""
    amt_str = re.sub(r"[\s\xA0]", "", amt_str)
    is_negative = '-' in amt_str
    amt_str = amt_str.replace('-', '')
    if len(amt_str) >= 3 and amt_str[-3] in '.,':
        sep = amt_str[-3]
        if sep == ',': 
            amt_str = amt_str.replace('.', '').rsplit(',', 1)[0].replace(',', '') + '.' + amt_str[-2:]
        else: 
            amt_str = amt_str.replace(',', '')
    else:
        amt_str = amt_str.replace(',', '') 
    val = float(amt_str)
    return -val if is_negative else val

def extract_visual_lines(page):
    """FIX 1: Reconstructs text based on physical visual coordinates to maintain column integrity."""
    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    if not words: return []
    
    clusters = []
    for word in words:
        placed = False
        for cluster in clusters:
            if abs(word['top'] - cluster[0]['top']) < 4: # 4 pt vertical tolerance for row alignment
                cluster.append(word)
                placed = True
                break
        if not placed:
            clusters.append([word])
            
    clusters.sort(key=lambda c: c[0]['top'])
    
    lines = []
    for cluster in clusters:
        cluster.sort(key=lambda w: w['x0']) # Read Left-to-Right
        line_str = ""
        last_x1 = cluster[0]['x0']
        for w in cluster:
            gap = w['x0'] - last_x1
            if gap > 12: # Significant gap indicates a column jump
                line_str += "  " 
            elif gap > 0:
                line_str += " "
            line_str += w['text']
            last_x1 = w['x1']
        lines.append(line_str.strip())
    return lines

@st.cache_data(show_spinner="📄 Aggregating & parsing global ledger...")
def parse_universal_ledger(files_data):
    raw_transactions = []
    warnings = []
    stated_closing_balances = {}
    
    for file_name, file_bytes in files_data:
        try:
            pdf_stream = io.BytesIO(file_bytes)
            with pdfplumber.open(pdf_stream) as pdf:
                # Pre-scan for year boundaries and printed end balances
                full_raw_text = "\n".join([page.extract_text() or "" for page in pdf.pages]).upper()
                year_matches = re.findall(r"\b(20[2-3][0-9])\b", full_raw_text)
                doc_years = sorted(list(set([int(y) for y in year_matches])))
                inferred_year = doc_years[-1] if doc_years else datetime.now().year
                
                # FIX 3: Detect if statement bridges December to January
                has_dec = bool(re.search(r"\b(?:DEC|DÉC)", full_raw_text))
                has_jan = bool(re.search(r"\b(?:JAN)", full_raw_text))
                
                last_balance = None
                
                for page in pdf.pages:
                    lines = extract_visual_lines(page)
                    for line in lines:
                        u_line = line.upper()

                        # FIX 5: Unglue single-digit dates (e.g., "3NOV" -> "3 NOV")
                        u_line = re.sub(rf"(?<!\s)(\d{{1,2}})({MONTHS_REGEX})\b", r" \1 \2", u_line)
                        u_line = re.sub(rf"\b({MONTHS_REGEX})(\d{{1,2}})(?!\s)", r"\1 \2 ", u_line)

                        # FIX 4: Explicitly capture stated closing balances for reconciliation
                        if any(kw in u_line for kw in ["ENDING BALANCE", "SOLDE FINAL", "CLOSING BALANCE", "NEW BALANCE"]):
                            amts = re.findall(AMOUNT_PATTERN, u_line)
                            if amts:
                                stated_bal = parse_amount(amts[-1])
                                # Temporarily store against the file name; will map to Month later
                                stated_closing_balances[file_name] = stated_bal
                            continue
                            
                        if any(kw in u_line for kw in ["BALANCE", "SOLDE", "TOTAL", "PAGE", "SUMMARY", "OVERVIEW"]):
                            continue

                        date_match = re.search(DATE_PATTERN, u_line)
                        if not date_match:
                            continue
                        
                        if date_match.group(1):
                            day, month = date_match.group(1), date_match.group(2)
                        else:
                            month, day = date_match.group(3), date_match.group(4)
                            
                        for fr, en in FR_EN_MONTHS.items():
                            if month.startswith(fr) or month == fr:
                                month = en
                                break
                        month = month[:3].capitalize()
                        
                        # Apply Year Boundary Logic
                        tx_year = inferred_year
                        if has_dec and has_jan and month.upper() == "DEC":
                            tx_year = inferred_year - 1
                        
                        try:
                            parsed_date = datetime.strptime(f"{tx_year}-{month}-{day.zfill(2)}", "%Y-%b-%d")
                        except ValueError:
                            continue

                        line_no_date = u_line[:date_match.start()] + " " + u_line[date_match.end():]
                        amts = re.findall(AMOUNT_PATTERN, line_no_date)
                        if not amts:
                            continue
                            
                        tx_amt = parse_amount(amts[0])
                        desc = u_line.replace(date_match.group(0), "").replace(amts[0], "").strip()[:80]
                        
                        is_credit = None
                        current_bal = None
                        
                        # FIX 2: Delta Math determines debit vs credit perfectly based on chronological balance shifts
                        if len(amts) >= 2:
                            current_bal = parse_amount(amts[-1])
                            if last_balance is not None:
                                delta = round(current_bal - last_balance, 2)
                                amt_rounded = round(abs(tx_amt), 2)
                                if delta == amt_rounded:
                                    is_credit = True
                                elif delta == -amt_rounded:
                                    is_credit = False
                        
                        # Keyword Fallback if Delta Math cannot trigger
                        if is_credit is None:
                            if tx_amt < 0:
                                is_credit = False
                                tx_amt = abs(tx_amt)
                            else:
                                strict_credits = ["INS ", "MSP ", "HDC ", "CMS ", "E-TRANSFER", "MOBILE DEPOSIT", "DEPOSIT", "DEPOT", "DÉPÔT", "INCOMING", "RECEPT", "TFR-FR", "RTN ", "REBATE", "PAYROLL", "PAIE", "CREDIT", "REMISE"]
                                strict_debits = ["NSLSC", "COOPERATORS", "ENMAX", "DIRECT ENERGY", "SEND E-TFR", "WORLDREMIT", "REMITLY", "LOAN", "BPY", "W/D", "FEE", "FRAIS", "NON-TD ATM", "TFR-TO", "RETRAIT", "ACHAT", "PURCHASE", "PAYMENT", "PAIEMENT", "CHQ", "CHEQUE", "WITHDRAWAL"]
                                
                                if any(kw in desc for kw in strict_credits):
                                    is_credit = True
                                elif any(kw in desc for kw in strict_debits):
                                    is_credit = False 
                                else:
                                    is_credit = False # Default constraint

                        if current_bal is not None:
                            last_balance = current_bal

                        tx_type = "Credit (Deposit)" if is_credit else "Debit (Withdrawal)"
                        is_etransfer = any(kw in desc for kw in ["E-TRANSFER", "E-TFR", "INTERAC", "TFR-FR"]) and is_credit
                        category = "Operational Revenue" if tx_type == "Credit (Deposit)" else "Standard Operating Expense"

                        for lender_name, meta in KNOWN_FUNDERS.items():
                            if any(kw in desc for kw in meta["keywords"]):
                                if "DÉPÔT" in desc or is_credit:
                                    continue
                                category = f"MCA Debt ({lender_name})"
                                break

                        if any(kw in desc for kw in ["NSF", "RETURNED ITEM", "OVERDRAWN", "FRAIS DE RETOUR"]):
                            category = "NSF / Overdraft Fee"
                        elif is_credit and not is_etransfer:
                            if any(ex in desc for ex in REVENUE_EXCLUSIONS):
                                category = "Non-Revenue Exclusion"

                        raw_transactions.append({
                            "Date_Obj": parsed_date,
                            "Date": parsed_date.strftime("%Y-%m-%d"),
                            "Month_Label": parsed_date.strftime("%B %Y"),
                            "Description": desc,
                            "Amount ($)": abs(tx_amt),
                            "Transaction Type": tx_type,
                            "Category": category,
                            "Source File": file_name
                        })

        except Exception as e:
            warnings.append(f"Failed to read {file_name}: {str(e)}")

    if not raw_transactions:
        return {}, pd.DataFrame(), warnings

    df_all = pd.DataFrame(raw_transactions)
    df_all = df_all.sort_values(by="Date_Obj").drop_duplicates(subset=["Date", "Description", "Amount ($)", "Transaction Type"]).reset_index(drop=True)

    month_summary_store = {}
    running_balance = 0.0 
    
    unique_months = df_all["Month_Label"].unique()
    for idx, m in enumerate(unique_months):
        m_df = df_all[df_all["Month_Label"] == m]
        
        m_credits = m_df[m_df["Transaction Type"] == "Credit (Deposit)"]["Amount ($)"].sum()
        m_debits = m_df[m_df["Transaction Type"] == "Debit (Withdrawal)"]["Amount ($)"].sum()
        
        start_bal = running_balance
        end_bal = start_bal + m_credits - m_debits
        
        # Link Stated Closing Balances to the final month of that file
        file_source = m_df["Source File"].iloc[-1]
        stated_bal = stated_closing_balances.get(file_source, None)

        month_summary_store[m] = {
            "Start Balance": start_bal,
            "Parsed End Balance": end_bal,
            "Stated End Balance": stated_bal if stated_bal is not None else end_bal,
            "Beginning Date": m_df["Date"].min(),
            "Ending Date": m_df["Date"].max(),
        }
        running_balance = end_bal 

    df_display = df_all.drop(columns=["Date_Obj"])
    return month_summary_store, df_display, warnings

# --- SECTION 1: BANK STATEMENT UPLOADER ---
st.subheader("1. Universal Statement Ingestion & Chronological Aggregation")

uploaded_files = st.file_uploader("Upload Bank Statements (PDFs)", type=["pdf"], accept_multiple_files=True)

auto_monthly_revenue = 0.0
total_nsf_count = 0
detected_funder_positions = []

if uploaded_files:
    if "df_transactions" not in st.session_state or st.sidebar.button("🔄 Re-process Uploaded Files"):
        files_payload = [(f.name, f.getvalue()) for f in uploaded_files]
        month_summary_store, df_tx, warnings = parse_universal_ledger(files_payload)
        st.session_state.month_summary_store = month_summary_store
        st.session_state.df_transactions = df_tx
        st.session_state.warnings = warnings

    df_tx = st.session_state.df_transactions
    month_summary_store = st.session_state.month_summary_store

    for w in st.session_state.get("warnings", []):
        st.warning(f"⚠️ {w}")

    if not df_tx.empty:
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
                    "Month_Label": st.column_config.TextColumn("Statement Month")
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic"
            )
            st.session_state.df_transactions = edited_df

        # --- RE-CALCULATE MONTHLY FINANCIAL BREAKDOWN ---
        monthly_table = []
        mca_tracker = defaultdict(lambda: {"total_amount": 0.0, "debit_count": 0})

        for month in df_tx["Month_Label"].unique():
            m_tx = edited_df[edited_df["Month_Label"] == month]
            m_info = month_summary_store.get(month, {})

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
            parsed_end_bal = m_info.get("Parsed End Balance", 0.0)
            stated_end_bal = m_info.get("Stated End Balance", 0.0)
            recon_delta = parsed_end_bal - stated_end_bal

            monthly_table.append({
                "Month": month,
                "Beginning Date": m_info.get("Beginning Date", "N/A"),
                "Ending Date": m_info.get("Ending Date", "N/A"),
                "Starting Balance": m_info.get("Start Balance", 0.0),
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
                "Parsed End Balance": parsed_end_bal,
                "Stated End Balance": stated_end_bal,
                "Reconciliation Delta": recon_delta,
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
            "Parsed End Balance": df_summary["Parsed End Balance"].sum(),
            "Stated End Balance": df_summary["Stated End Balance"].sum(),
            "Reconciliation Delta": df_summary["Reconciliation Delta"].sum(),
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
            "Parsed End Balance": df_summary["Parsed End Balance"].mean(),
            "Stated End Balance": df_summary["Stated End Balance"].mean(),
            "Reconciliation Delta": df_summary["Reconciliation Delta"].mean(),
            "# NSF": df_summary["# NSF"].mean()
        }

        df_display = pd.concat([df_summary, pd.DataFrame([total_row, avg_row])], ignore_index=True)

        tab_bank, tab_debt, tab_rev, tab_non_rev, tab_flags = st.tabs([
            "Bank Statement Summary", "Debt Summary", "Revenue Summary", "Non-Revenue Summary", "Flags"
        ])

        with tab_bank:
            st.markdown("### Bank Statement Aggregation Summary")
            
            def highlight_totals(s):
                color_arr = [''] * len(s)
                if s["Month"] in ["TOTAL", "Average"]:
                    color_arr = ['color: red; font-weight: bold;'] * len(s)
                # Highlight Reconciliation Failures
                if abs(s["Reconciliation Delta"]) > 2.0 and s["Month"] not in ["TOTAL", "Average"]:
                    idx = s.index.get_loc("Reconciliation Delta")
                    color_arr[idx] = 'background-color: yellow; color: black; font-weight: bold;'
                return color_arr

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
                "Parsed End Balance": "${:,.2f}",
                "Stated End Balance": "${:,.2f}",
                "Reconciliation Delta": "${:,.2f}",
                "# NSF": "{:,.2f}"
            })
            st.dataframe(formatted_df, use_container_width=True, hide_index=True)
            st.caption("🔍 **Reconciliation Check**: 'Parsed End Balance' is calculated mathematically from all extracted transactions. 'Stated End Balance' is read directly from the bank's summary line. 'Reconciliation Delta' flags any missing or dropped transactions during extraction.")

        with tab_debt:
            st.markdown("### 💳 Classified Debt & MCA Loan Debits Bucket")
            debt_tx = edited_df[edited_df["Category"].str.contains("MCA Debt", na=False)]
            m_filter = st.multiselect("Filter by Month:", options=edited_df["Month_Label"].unique(), key="debt_m_filter")
            if m_filter:
                debt_tx = debt_tx[debt_tx["Month_Label"].isin(m_filter)]
            st.dataframe(debt_tx.style.format({"Amount ($)": "${:,.2f}"}), use_container_width=True, hide_index=True)

        with tab_rev:
            st.markdown("### 🟢 Operational Revenue Bucket")
            rev_tx = edited_df[edited_df["Category"] == "Operational Revenue"]
            m_filter_r = st.multiselect("Filter by Month:", options=edited_df["Month_Label"].unique(), key="rev_m_filter")
            if m_filter_r:
                rev_tx = rev_tx[rev_tx["Month_Label"].isin(m_filter_r)]
            st.dataframe(rev_tx.style.format({"Amount ($)": "${:,.2f}"}), use_container_width=True, hide_index=True)

        with tab_non_rev:
            st.markdown("### 🛑 Non-Revenue Exclusions Bucket")
            non_rev_tx = edited_df[edited_df["Category"] == "Non-Revenue Exclusion"]
            m_filter_nr = st.multiselect("Filter by Month:", options=edited_df["Month_Label"].unique(), key="non_rev_m_filter")
            if m_filter_nr:
                non_rev_tx = non_rev_tx[non_rev_tx["Month_Label"].isin(m_filter_nr)]
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
