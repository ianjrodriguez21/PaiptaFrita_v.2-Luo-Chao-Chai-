from .queries import handle


def lambda_handler(event, context):
    return handle(event, "dashboard")
