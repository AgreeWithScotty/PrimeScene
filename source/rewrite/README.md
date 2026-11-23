# Smooth Prime 0.2.37 package cleanup

Removes nine unused installed-package files: two root background images, three old button textures and four regional menu JSON files. All retained Python/UI/art/font bytes match 0.2.36; only release metadata changes. The release packager checks runtime Python/XML/JSON asset references, actual manifest/font paths and the dynamic genre artwork map before creating an archive. Full source remains in the installable ZIP. Historical release archives retain their original contents.
