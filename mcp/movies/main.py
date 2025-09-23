import os
import json
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import tmdbsimple as tmdb
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Movie MCP Server")

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configurar APIs
tmdb.API_KEY = "f0a8429722a9279a0276fcc3204b6ec4"


@app.post("/mcp/jsonrpc")
async def handle_mcp_request(request: dict):
    method = request.get("method")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024.11",
                "capabilities": {
                    "tools": {
                        "listChanged": True
                    }
                },
                "serverInfo": {
                    "name": "movie-mcp-server",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "search_movie",
                        "description": "Buscar información de una película por título",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Título de la película"}
                            },
                            "required": ["title"]
                        }
                    },
                    {
                        "name": "get_movie_recommendations",
                        "description": "Obtener recomendaciones basadas en preferencias",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "genres": {"type": "array", "items": {"type": "string"},
                                           "description": "Géneros preferidos"},
                                "min_rating": {"type": "number", "description": "Rating mínimo"}
                            }
                        }
                    },
                    {
                        "name": "get_random_movie",
                        "description": "Obtener una película aleatoria con información básica",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    }
                ]
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        try:
            if tool_name == "search_movie":
                result = await search_movie(arguments.get("title", ""))
            elif tool_name == "get_movie_recommendations":
                result = await get_movie_recommendations(arguments)
            elif tool_name == "get_random_movie":
                result = await get_random_movie()
            else:
                result = {"error": "Herramienta no encontrada"}

            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": result
            }

        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32000,
                    "message": f"Error ejecutando herramienta: {str(e)}"
                }
            }

    return {"jsonrpc": "2.0", "id": request.get("id"), "error": "Método no soportado"}


async def search_movie(title: str):
    """Buscar información de película en TMDB"""
    try:
        print(f" Buscando película: {title}")

        search = tmdb.Search()
        response = search.movie(query=title)

        if not search.results:
            return {"error": "Película no encontrada en TMDB"}

        movie = search.results[0]
        movie_id = movie['id']

        # Obtener detalles completos
        movie_details = tmdb.Movies(movie_id).info()

        # Obtener plataformas de streaming
        streaming_platforms = await get_streaming_info(movie_id)

        # Obtener películas similares
        similar_movies = await get_similar_movies(movie_id)

        return {
            "title": movie_details.get('title', 'Desconocido'),
            "overview": movie_details.get('overview', 'Sin sinopsis disponible'),
            "genres": [genre['name'] for genre in movie_details.get('genres', [])],
            "rating": movie_details.get('vote_average', 0),
            "release_date": movie_details.get('release_date', 'Desconocida'),
            "streaming_platforms": streaming_platforms,
            "similar_movies": similar_movies
        }

    except Exception as e:
        print(f" Error en search_movie: {e}")
        return {"error": f"No se pudo buscar la película: {str(e)}"}


async def get_streaming_info(movie_id: int):
    """Obtener información de plataformas de streaming desde TMDB"""
    try:
        movie = tmdb.Movies(movie_id)
        providers = movie.watch_providers()

        streaming_platforms = []
        if providers and 'results' in providers:
            # Buscar en diferentes regiones
            for region in ['US', 'MX', 'ES']:
                if region in providers['results']:
                    flatrate = providers['results'][region].get('flatrate', [])
                    streaming_platforms.extend([provider['provider_name'] for provider in flatrate])

        return list(set(streaming_platforms))[:3] if streaming_platforms else ["Información no disponible"]

    except Exception as e:
        print(f" Error en get_streaming_info: {e}")
        return ["Información no disponible"]


async def get_similar_movies(movie_id: int):
    """Obtener películas similares"""
    try:
        movie = tmdb.Movies(movie_id)
        similar = movie.similar_movies()

        return [
            {
                "title": movie['title'],
                "rating": movie['vote_average'],
                "year": movie['release_date'][:4] if movie.get('release_date') else "N/A"
            }
            for movie in similar.get('results', [])[:3]
        ]

    except Exception as e:
        print(f" Error en get_similar_movies: {e}")
        return []


async def get_movie_recommendations(preferences: dict):
    """Obtener recomendaciones personalizadas"""
    try:
        genres = preferences.get('genres', [])
        min_rating = preferences.get('min_rating', 7.0)

        discover = tmdb.Discover()
        movies = discover.movie(
            with_genres="|".join(genres) if genres else "",
            vote_average_gte=min_rating,
            sort_by='popularity.desc',
            page=1
        )

        return {
            "recommendations": [
                {
                    "title": movie['title'],
                    "rating": movie['vote_average'],
                    "overview": (movie['overview'][:100] + "...") if movie.get('overview') else "Sin descripción",
                    "year": movie['release_date'][:4] if movie.get('release_date') else "N/A"
                }
                for movie in movies.get('results', [])[:3]
            ]
        }

    except Exception as e:
        print(f" Error en get_movie_recommendations: {e}")
        return {"error": str(e)}


async def get_random_movie():
    """Obtener película aleatoria de las populares"""
    try:
        movies = tmdb.Movies()
        popular = movies.popular(page=1)

        if popular['results']:
            import random
            movie = random.choice(popular['results'])

            return {
                "title": movie['title'],
                "overview": movie.get('overview', 'Sin sinopsis disponible'),
                "rating": movie.get('vote_average', 0),
                "release_date": movie.get('release_date', 'Desconocida'),
                "popularity": movie.get('popularity', 0)
            }
        else:
            return {"error": "No se encontraron películas populares"}

    except Exception as e:
        print(f" Error en get_random_movie: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)