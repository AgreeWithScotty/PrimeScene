# Smooth Prime 0.2.29: category-specific empty states

The generic Catalog Request Failed display is removed. Movies/TV, each selected genre, Sports, each selected service, In Progress, Purchases, Search, service/genre directories, seasons and episodes have their own contextual empty-state message. Existing In Progress wording is retained. Service/genre/show names are used for display, never logged as private context.

A successful native zero-result structure can be empty: explicit hasMoreItems=false with no entities, or a recognized successful native catalog body with empty containers/entities and no continuation. Authentication forms, provider errors and unknown structures remain failures. Actual request failures get category-specific unavailable-right-now/retry wording rather than a false no-titles claim. Full HTTP/redirect/parser/exception diagnostics remain. Populated cards and descriptions remain intact after a later page error.

Exact display-text pairs for twelve contexts are recorded as copy examples, not provider/Kodi simulations. Existing real Fantasy/Apple responses retain their cards and are not classified empty. Source/import checks and archive/public download verification are concrete artifact evidence; no signed-in or remote UI success is claimed. All skin files, startup, art, TV hierarchy, paging, playback return and account handling remain.
