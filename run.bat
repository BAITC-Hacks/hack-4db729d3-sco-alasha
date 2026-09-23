@echo off
pip install -r requirements.txt
python pipeline.py --data data --out out
streamlit run app.py
