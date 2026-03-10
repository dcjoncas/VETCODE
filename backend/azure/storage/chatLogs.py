import azure.storage.client as client
import azure.storage.processingFunctions as processing
from datetime import datetime, timedelta

def scheduleChat(profileid: str):
    weekFromNow = (datetime.now() + timedelta(weeks=1)).date()

    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = "INSERT INTO aichatlogs (personid, enddate) VALUES (%s, %s)"
    
    cur.execute(query, (profileid, weekFromNow))
    print("success!")
    print(cur.rowcount)

    # TODO: Send email with link to the candidate

    conn.commit()
    conn.close()