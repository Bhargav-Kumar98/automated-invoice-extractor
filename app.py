import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
from typing_extensions import TypedDict
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import io
import os

# Define the expected JSON schema
class Invoice(TypedDict):
    invoice_number: str
    customer_name: str
    gross_price: str
    tax: str
    total_price: str

# Configure API keys from Streamlit secrets
genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
GOOGLE_SHEETS_CREDENTIALS = "invoice-extractor-449819-d6eaa6d5b1ce.json"  # Your service account JSON

# Streamlit app configuration
st.set_page_config(
    page_title="Automated Invoice Extractor",
    layout="wide"
)

# UI Header
st.title("Automated Invoice Extractor")
st.markdown("""
Upload an invoice image or use your camera to **automatically extract data and update Google Sheets**.
""")

# Create separate tabs for file upload and camera input
tabs = st.tabs(["Upload File", "Camera"])
with tabs[0]:
    uploaded_file = st.file_uploader("Upload Invoice Image", type=["png", "jpg", "jpeg"])
with tabs[1]:
    camera_image = st.camera_input("Take Photo of Invoice")

# Determine which image source to use (only one should be provided)
img_source = None
if uploaded_file is not None:
    img_source = uploaded_file
elif camera_image is not None:
    img_source = camera_image

# Process button: image processing and Google Sheets update are triggered only when an image is provided
if st.button("⚡ Process & Update Automatically", type="primary"):
    if not img_source:
        st.warning("Please provide an invoice image using one of the tabs.")
        st.stop()

    with st.spinner("Processing invoice..."):
        try:
            # Load and process image data
            img = Image.open(io.BytesIO(img_source.getvalue()))
            
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            generation_config = genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=Invoice
            )
            
            # Updated prompt with a note if not a valid invoice.
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
            
            # Parse and validate response
            try:
                invoice_data = json.loads(response.text)
                # Check if all fields are "-"
                if all(value.strip() == "-" for value in invoice_data.values()):
                    st.warning("⚠️ Invoice cannot be extracted from the provided image.")
                    st.stop()
            except json.JSONDecodeError:
                st.warning("⚠️ Invoice cannot be extracted from the provided image.")
                st.stop()

            # Tax calculation logic (if tax is provided as a percentage)
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

            # Update Google Sheets only if valid data was extracted
            def update_google_sheet(invoice_data):
                try:
                    # Authenticate with Google Sheets
                    scope = ["https://spreadsheets.google.com/feeds",
                             "https://www.googleapis.com/auth/drive"]
                    credentials = ServiceAccountCredentials.from_json_keyfile_name(
                        GOOGLE_SHEETS_CREDENTIALS, scope)
                    client = gspread.authorize(credentials)
                    
                    spreadsheet = client.open("Invoices")
                    worksheet = spreadsheet.sheet1
                    
                    # Fetch existing data and set headers if needed
                    rows = worksheet.get_all_values()
                    if not rows:
                        worksheet.append_row(["Invoice Number", "Customer Name", "Gross Price", "Tax", "Total Price"])
                    elif rows[0] != ["Invoice Number", "Customer Name", "Gross Price", "Tax", "Total Price"]:
                        worksheet.insert_row(["Invoice Number", "Customer Name", "Gross Price", "Tax", "Total Price"], 1)
                    
                    # Prepare record to update or append
                    record = [
                        invoice_data.get("invoice_number", "-"),
                        invoice_data.get("customer_name", "-"),
                        str(invoice_data.get("gross_price", "-")),
                        str(invoice_data.get("tax", "-")),
                        str(invoice_data.get("total_price", "-"))
                    ]
                    
                    # Check for existing invoice entry
                    existing_ids = worksheet.col_values(1)
                    if invoice_data.get("invoice_number") in existing_ids:
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
            
            sheet_success, sheet_result = update_google_sheet(invoice_data)
            if not sheet_success:
                st.error(f"❌ Sheet update failed: {sheet_result}")
                st.stop()
            
            # Display the extracted invoice information
            st.subheader("✅ Processing Complete! Google Sheet Updated Successfully.")
            
        except Exception as e:
            st.error(f"❌ Processing failed: {str(e)}")
            st.stop()