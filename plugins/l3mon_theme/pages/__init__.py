"""One module per page of the theme (SP6). Each module may define

    install(app)     hooks of the page (a before_request, a view), called once when the plugin loads
    and, through `registry.value`, the values its adapter template reads from `l3mon`

A page adds a module here and edits no shared file: the plugin imports every module of this folder by itself, in the order of
their names (registry.discover).
"""
