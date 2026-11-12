"""Optional Google Sheets export, imported only when configured."""
import json, os

def sync(data_dir="data"):
    raw=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        print("Google Sheets sync skipped: GOOGLE_SERVICE_ACCOUNT_JSON is not set.")
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds=Credentials.from_service_account_info(json.loads(raw),scopes=[
          "https://www.googleapis.com/auth/spreadsheets"])
        book=gspread.authorize(creds).open_by_key(
          "1DVRl_AuAYm4uX9VLbEL5gcTSF6Fus46Fuzo5rdDj6Wo")
        latest=json.load(open(os.path.join(data_dir,"latest.json")))
        stats=json.load(open(os.path.join(data_dir,"stats.json")))
        history=open(os.path.join(data_dir,"snapshots.csv")).read()
        for name, values in [("Latest",latest["top"]),("UID Stats",list(stats.values())),
                             ("History", [line.split(",") for line in history.splitlines()])]:
            try: sheet=book.worksheet(name); sheet.clear()
            except Exception: sheet=book.add_worksheet(name,rows=100,cols=30)
            if values:
                if isinstance(values[0],dict):
                    keys=list(values[0]); values=[keys]+[[v.get(k) for k in keys] for v in values]
                sheet.update("A1",values)
        print("Google Sheets sync complete.")
    except Exception as exc:
        print(f"Google Sheets sync skipped: {exc}")

if __name__ == "__main__":
    sync()
