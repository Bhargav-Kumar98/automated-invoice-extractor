import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
from typing_extensions import TypedDict
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import io
import os

# Define the expected JSON schema for the invoice extraction
class Invoice(TypedDict):
    invoice_number: str
    customer_name: str
    gross_price: str
    tax: str
    total_price: str

# Configure the Generative API key from Streamlit secrets
genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

# --- CACHED GOOGLE SHEETS CLIENT ---
# Use st.experimental_singleton (or st.cache_resource in later versions) to reuse the connection
@st.experimental_singleton
def get_gs_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    # Read service account credentials from st.secrets. Ensure that your secrets file contains a table "gcp_service_account"
    creds = st.secrets["gcp_service_account"]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds, scope)
    client = gspread.authorize(credentials)
    return client

# Function to update the Google Sheet with invoice data
def update_google_sheet(invoice_data):
    try:
        # Use the cached Google Sheets client
        client = get_gs_client()
        spreadsheet = client.open("Invoices")
        worksheet = spreadsheet.sheet1

        # Get existing data and ensure headers are set correctly
        rows = worksheet.get_all_values()
        headers = ["Invoice Number", "Customer Name", "Gross Price", "Tax", "Total Price"]
        if not rows:
            worksheet.append_row(headers)
        elif rows[0] != headers:
            worksheet.insert_row(headers, 1)

        # Prepare record from invoice_data
        record = [
            invoice_data.get("invoice_number", "-"),
            invoice_data.get("customer_name", "-"),
            str(invoice_data.get("gross_price", "-")),
            str(invoice_data.get("tax", "-")),
            str(invoice_data.get("total_price", "-"))
        ]

        # Check for duplicate invoice_number to update existing record
        existing_ids = worksheet.col_values(1)
        if invoice_data.get("invoice_number") in existing_ids:
            # Update the row if invoice exists
            row_index = existing_ids.index(invoice_data["invoice_number"]) + 1
            for col_num, value in enumerate(record, start=1):
                worksheet.update_cell(row_index, col_num, value)
            action = "updated"
        else:
            worksheet.append_row(record)
            action = "added"

        return True, action
    except Exception as e:
        return False, str(e)

# Streamlit app configuration
st.set_page_config(page_title="Auto Invoice Processor", page_icon="🤖", layout="wide")

# UI Header
st.title("🤖 Automated Invoice Processing")
st.markdown("""
Upload an invoice image or use your camera to **automatically extract data and update Google Sheets**.
""")

# Create separate tabs for file upload and camera input
tabs = st.tabs(["Upload File", "Camera"])
with tabs[0]:
    uploaded_file = st.file_uploader("Upload Invoice Image", type=["png", "jpg", "jpeg"])
with tabs[1]:
    camera_image = st.camera_input("Take Photo of Invoice")

# Determine image source (only one option should be provided)
img_source = None
if uploaded_file is not None:
    img_source = uploaded_file
elif camera_image is not None:
    img_source = camera_image

# Processing button: triggers processing and Google Sheets update only if an image is provided
if st.button("⚡ Process & Update Automatically", type="primary"):
    if not img_source:
        st.warning("Please provide an invoice image using one of the tabs.")
        st.stop()

    with st.spinner("Processing invoice..."):
        try:
            # Load image from the provided source
            img = Image.open(io.BytesIO(img_source.getvalue()))
            
            # Set up the Generative AI model and response configuration using the Invoice schema
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            generation_config = genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=Invoice
            )
            
            # Revised prompt instructs: if the image is not a valid invoice, return JSON with all fields as "-"
            prompt = """Extract invoice details including:
- Customer Name (exact match)
- Invoice Number/ID (any format)
- Gross Price (pre-tax)
- Tax (value or %)
- Total Price (final amount)

Rules:
1. Return JSON with "-" for missing fields.
2. Calculate tax if percentage given.
3. Verify total = gross + tax.
4. Maintain original formatting.
Note: If the provided image is not a valid invoice, return JSON with all fields as "-" to indicate that the invoice cannot be extracted."""
            
            response = model.generate_content(
                [prompt, img],
                generation_config=generation_config
            )
            
            # Parse and validate response JSON
            try:
                invoice_data = json.loads(response.text)
                # Check if every field in the invoice_data is "-" (after stripping whitespace)
                if all(value.strip() == "-" for value in invoice_data.values()):
                    st.warning("⚠️ Invoice cannot be extracted from the provided image.")
                    st.stop()
            except json.JSONDecodeError:
                st.warning("⚠️ Invoice cannot be extracted from the provided image.")
                st.stop()

            # Tax calculation logic if tax is given as a percentage
            gross_price = invoice_data.get("gross_price", "-")
            tax = invoice_data.get("tax", "-")
            try:
                gross_value = float(str(gross_price).replace(",", "").replace("$", ""))
                if "%" in str(tax):
                    tax_percent = float(str(tax).replace("%", ""))
                    tax_value = round(gross_value * (tax_percent / 100), 2)
                    invoice_data["tax"] = str(tax_value)
                    invoice_data["total_price"] = str(round(gross_value + tax_value, 2))
            except Exception:
                pass

            # Update the Google Sheet with the processed invoice_data
            sheet_success, sheet_result = update_google_sheet(invoice_data)
            if not sheet_success:
                st.error(f"❌ Sheet update failed: {sheet_result}")
                st.stop()
            
            # Display the extracted invoice information
            st.subheader("✅ Processing Complete")
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("Customer", invoice_data.get("customer_name", "-"))
                st.metric("Invoice Number", invoice_data.get("invoice_number", "-"))
            with col_b:
                st.metric("Gross Price", gross_price)
                st.metric("Total Price", invoice_data.get("total_price", "-"))
            
            st.success(f"Google Sheet {sheet_result} successfully!")
            st.json(invoice_data, expanded=False)
            
        except Exception as e:
            st.error(f"❌ Processing failed: {str(e)}")
            st.stop()