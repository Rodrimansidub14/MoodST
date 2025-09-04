from llm import ask_llm
from session import Session
from logger import log_mcp

def main():
    session = Session()
    print("Host listo. Comandos: :log, :reset, :exit")

    while True:
        q = input("> ")
        if q == ":exit":
            break
        if q == ":reset":
            session = Session()
            print("Sesión reiniciada.")
            continue
        if q == ":log":
            print("Revisa los archivos en ./logs/")
            continue

        session.add_turn(q)
        ans = ask_llm(q, session.get_context())
        print(ans)

        # simulación de log MCP (por ahora dummy)
        log_mcp({"event": "llm_exchange", "user": q, "assistant": ans})

if __name__ == "__main__":
    main()
