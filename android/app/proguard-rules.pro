# kotlinx.serialization, OkHttp, DataStore and Compose ship their own R8 rules.
# The API models are kept explicitly so a rename never breaks the JSON mapping.
-keepattributes *Annotation*, InnerClasses
-keepclassmembers @kotlinx.serialization.Serializable class dev.skrpld.musicloader.data.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class dev.skrpld.musicloader.data.**$$serializer { *; }
