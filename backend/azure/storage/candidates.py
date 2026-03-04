import client

def testConnection():
    conn = client.getConnection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM person LIMIT 1;")
    result = cur.fetchall()
    print(f'Test query result: {result}')

    conn.close()

def searchCandidatesByNameEmail(query: str, limit: int = 5):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.*, professional.email FROM person JOIN professional ON person.id = professional.id WHERE person.firstname ILIKE '%{query}%' OR person.lastname ILIKE '%{query}%' OR person.goesbyname ILIKE '%{query}%' OR professional.email ILIKE '%{query}%' ORDER BY id DESC LIMIT {limit};"
    
    cur.execute(query)
    results = cur.fetchall()
    print(f'Search results for "{query}": {results}')

    conn.close()
    
    return results