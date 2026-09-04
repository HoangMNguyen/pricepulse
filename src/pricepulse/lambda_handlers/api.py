from mangum import Mangum

from pricepulse.api.app import create_app

handler = Mangum(create_app(), lifespan="off")
