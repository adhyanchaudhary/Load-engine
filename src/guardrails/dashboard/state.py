"""UI selections and idempotency intent only; jobs/results remain persistent."""
import hashlib
import json
from uuid import uuid4
import streamlit as st


def request_key(action,payload):
    token=st.session_state.setdefault('intent:'+action,str(uuid4()))
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:32]
    return action+':'+token+':'+digest


def new_intent(action):st.session_state['intent:'+action]=str(uuid4())


def selected(key,options,label):
    current=st.session_state.get(key)
    if current not in options:st.session_state.pop(key,None)
    return st.selectbox(label,options,index=options.index(current) if current in options else 0,key=key) if options else None
