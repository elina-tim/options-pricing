py -3.12 -m venv venv

venv\Scripts\activate

pip install "zstandard==0.23.0"
pip install driftpy anchorpy solana solders streamlit plotly
pip install "zstandard==0.23.0" --force-reinstall
pip install driftpy anchorpy solana solders streamlit plotly --no-deps
pip install aiohttp anchorpy-core solders solana borsh-construct based58 aiodns

pip install -r requirements.txt
streamlit run C:\Users\elina\PycharmProjects\options-pricing\app.py