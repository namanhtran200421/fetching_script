from fastapi import FastAPI
from app.config import WORDPRESS_APP_PASSWORD, WORDPRESS_BASE_URL, WORDPRESS_USERNAME, MEC_API_TOKEN
from app.clients.wordpress_client import WordPressClient
from app.clients.mec_client import MecCalendarClient
from app.ingestion.source_registry import WORDPRESS_SOURCES
from app.ingestion.fetcher import IngestionFetcher
app = FastAPI()

#wordpress client
# wp/v2/
wordpress_client = WordPressClient(
    base_url = WORDPRESS_BASE_URL, 
    username = WORDPRESS_USERNAME,
    application_password= WORDPRESS_APP_PASSWORD
)

#mec client
# mec/v1.0/
mec_client = MecCalendarClient(
    base_url=WORDPRESS_BASE_URL,
    username=WORDPRESS_USERNAME,
    application_password=WORDPRESS_APP_PASSWORD,
    api_token= MEC_API_TOKEN
)

ingestion_fetcher = IngestionFetcher(wordpress_client=wordpress_client)

# avoid reading restricted data, only include relevant ones for this project

@app.get('/health')
def health():
    return { "status" : "ok"}

# get 1 page research paper
@app.get("/research/page/{page}")
def research_page(page:int):
    response = wordpress_client.get_page(endpoint='academic_paper', page=page)
    papers = response.json()

    return { 
        "page" : page,
        "total_papers": int(response.headers['X-WP-Total']
        ),
        "total_pages": int(response.headers['x-WP-TotalPages']
        ),
        "papers_on_this_page": len(papers),
        "papers":papers
    }


@app.get('/research/count')
def research_count():
    papers = wordpress_client.get_all(
        'academic_paper'
        #add more later
    )
    return {
        'count':len(papers)
    }

@app.get('/sources/counts')
def source_counts():
    counts = {}
    # loops over the dict WORDPRESS_SOURCE
    for source_name, source_config in WORDPRESS_SOURCES.items():
        endpoint = source_config["endpoint"]

        items = wordpress_client.get_all(endpoint=endpoint, fields='id')
        # build source
        counts[source_name] = len(items)

    return counts


@app.get("/calendar/event/{event_id}/next-occurrences")
def calendar_next_occurrences(event_id: int):

    response = mec_client.get_next_occurrences(
        event_id=event_id
    )

    return response.json()


@app.get("/calendar/definitions/count")
def calendar_definitions_count():

    events = wordpress_client.get_all(
        "mec-events"
    )
    return {
        "published_event_definitions": len(events)
    }


'''
INGESTION BEGINS HERE
'''

@app.get('/ingestion/sample/{source_name}')
def ingestion_sample(source_name:str):
    items = ingestion_fetcher.fetch_source(source_name)

    if not items:
        return {
            'source': source_name, 
            'count': 0
        }

    first_item = items[0]

    return {
        'source': source_name,
        'count': len(items),
        'first_item': first_item
    }




