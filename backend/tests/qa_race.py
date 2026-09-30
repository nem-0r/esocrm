import asyncio, sys
from tests.support import ADMIN, BASE, login
async def main():
    a = await login(ADMIN)
    ids=[i["id"] for i in (await a.get(f"{BASE}/conversations",params={"limit":5})).json()["items"]]
    cid=ids[0]
    rs = await asyncio.gather(*[a.post(f"{BASE}/conversations/{cid}/messages", json={"text": f"гонка {i}"}) for i in range(10)])
    print("post_message x10:", sorted(r.status_code for r in rs))
    src=cid; tgt=ids[1]
    m=(await a.post(f"{BASE}/conversations/{src}/messages", json={"text":"src"})).json()["id"]
    await asyncio.sleep(2)
    rs = await asyncio.gather(*[a.post(f"{BASE}/conversations/{tgt}/forward", json={"source_conversation_id":src,"message_ids":[m]}) for i in range(10)])
    print("forward x10:", sorted(r.status_code for r in rs))
    rs = await asyncio.gather(*[a.post(f"{BASE}/conversations/{tgt}/forward", json={"source_conversation_id":src,"message_ids":[m]}) for i in range(10)], *[a.post(f"{BASE}/conversations/{tgt}/messages", json={"text": "смесь"}) for i in range(10)])
    print("forward+post x20:", sorted(r.status_code for r in rs))
asyncio.run(main())
