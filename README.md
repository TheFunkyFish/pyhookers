# pyhookers
Collection of hooking scripts mostly fastAPI and uvicorn

# Use Open Code with your remote servers. The most simplistitic of a proxy to llama.cpp or probably most inference engines that could be exposed. Use this to lock down the LLM to local host and this to your network or credentials, or BOTH!!!!

Whatever port, whatever host, reload if making changes to your implementation.
Dont forgeet your firewall opening.

python3 -m uvicorn open_code_hooker:app --host 0.0.0.0 --port 8082 --reload

** Use at your own risk. There is not authorization built into this as of October 28th,2026. Build your own simple routine that will allow open code to authenticate when it makes its requests/
