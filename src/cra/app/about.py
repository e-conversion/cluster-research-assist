"""What the service says about itself to someone who has not signed in.

A federation such as DFN-AAI registers a service only when anyone can find out
what it offers. Each deployment may write its own text (the
``service_description`` policy key); until it does, this one is built from the
cluster's configuration, so a fresh instance already has something true to say.
"""

from cra.config.settings import Settings

OFFERS = """Once signed in, you can:

- ask questions in a chat, answered from the library with the papers cited
- explore a map of the cluster's publications, arranged by topic
- see who works with whom in a graph of its research groups"""


def default_description(settings: Settings) -> str:
    """Markdown describing the service, from the deployment's configuration."""
    opening = (
        f"{settings.cluster_display_name} answers questions about the research "
        f"of {settings.cluster_description}"
    )
    facts = []
    if settings.cluster_funding_body:
        facts.append(f"funded by {settings.cluster_funding_body}")
    if settings.cluster_host_institutions:
        facts.append("hosted at " + " and ".join(settings.cluster_host_institutions))
    if facts:
        opening += ", " + ", ".join(facts)
    opening += (
        ". It works from a library of the cluster's publications and the profiles "
        "of its principal investigators."
    )
    if settings.cluster_website:
        opening += f" More about the cluster: <{settings.cluster_website}>."

    if settings.oidc_enabled:
        accounts = (
            "Accounts are for members of the cluster. They sign in with their "
            "university account through DFN-AAI, or with a password an "
            "administrator set up."
        )
    else:
        accounts = (
            "Accounts are for members of the cluster and are set up by an "
            "administrator."
        )
    return f"{opening}\n\n{OFFERS}\n\n{accounts}"
