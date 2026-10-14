# IPL Batters Dashboard

## Run the dashboard

1. Extract this folder.
2. Open the folder in VS Code.
3. Open Terminal in the project folder.
4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Start Streamlit:

   ```bash
   streamlit run app.py
   ```

The dashboard currently uses demo data so it runs immediately. Replace the demo data
inside `load_data()` in `app.py` with your real IPL dataset before using it for analysis.
