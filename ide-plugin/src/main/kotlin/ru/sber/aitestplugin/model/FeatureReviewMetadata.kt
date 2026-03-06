package ru.sber.aitestplugin.model

import com.intellij.openapi.util.Key

data class FeatureReviewMetadata(
    val projectRoot: String,
    val targetPath: String?,
    val overwriteExisting: Boolean,
    val planId: String?,
    val selectedScenarioId: String?,
    val originalFeatureText: String
)

val FEATURE_REVIEW_METADATA_KEY: Key<FeatureReviewMetadata> =
    Key.create("ru.sber.aitestplugin.feature.review.metadata")
