import streamlit as st

try:
    st.write("Accessing secrets...")
    if "TEST" in st.secrets:
        st.write("Found TEST")
    else:
        st.write("TEST not found")
except FileNotFoundError:
    st.write("Caught FileNotFoundError")
except Exception as e:
    st.write(f"Caught {type(e).__name__}: {e}")
