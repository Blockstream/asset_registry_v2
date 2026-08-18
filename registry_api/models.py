import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Sequence,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from registry_api.db import Base
from registry_api.constants import IconProposalStatus

audit_sequence_global = Sequence("audit_sequence_global")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str | None] = mapped_column(Text, nullable=True)
    precision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    contract_extra_fields: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    domain_verification_method: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="http"
    )
    initial_issuer_pubkey: Mapped[str] = mapped_column(String(66), nullable=False)
    initial_issuer_pubkey_source: Mapped[str] = mapped_column(Text, nullable=False)
    current_issuer_pubkey: Mapped[str] = mapped_column(String(66), nullable=False)
    mutable_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    active_icon_proposal_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "asset_icon_proposals.icon_proposal_uuid",
            name="assets_active_icon_proposal_fk",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )

    mutable_metadata: Mapped["AssetMutableMetadata"] = relationship(
        back_populates="asset", uselist=False
    )
    trading_venues: Mapped[list["AssetTradingVenue"]] = relationship(
        back_populates="asset"
    )
    category_tags: Mapped[list["AssetCategoryTag"]] = relationship(
        back_populates="asset"
    )
    custom_attributes: Mapped[list["AssetCustomAttribute"]] = relationship(
        back_populates="asset"
    )
    admin_annotations: Mapped["AssetAdminAnnotation"] = relationship(
        back_populates="asset", uselist=False
    )
    icon: Mapped["AssetIconProposal | None"] = relationship(
        foreign_keys=[active_icon_proposal_uuid],
        uselist=False,
        post_update=True,
    )

    __table_args__ = (
        CheckConstraint(
            "asset_id ~ '^[0-9a-f]{64}$'", name="assets_asset_id_format_chk"
        ),
        CheckConstraint(
            "initial_issuer_pubkey ~ '^(02|03)[0-9a-f]{64}$'",
            name="assets_initial_pubkey_format_chk",
        ),
        CheckConstraint(
            "current_issuer_pubkey ~ '^(02|03)[0-9a-f]{64}$'",
            name="assets_current_pubkey_format_chk",
        ),
        CheckConstraint("contract_version >= 0", name="assets_contract_version_chk"),
        CheckConstraint(
            "jsonb_typeof(contract_extra_fields) = 'object'",
            name="assets_contract_extra_fields_object_chk",
        ),
        CheckConstraint(
            "mutable_schema_version >= 1", name="assets_mutable_schema_version_chk"
        ),
        Index("assets_asset_id_idx", "asset_id"),
        Index(
            "assets_asset_id_prefix_idx",
            "asset_id",
            postgresql_ops={"asset_id": "text_pattern_ops"},
        ),
        Index("assets_domain_idx", "domain"),
        Index(
            "assets_ticker_idx", "ticker", postgresql_ops={"ticker": "text_pattern_ops"}
        ),
        Index("assets_name_idx", "name", postgresql_ops={"name": "text_pattern_ops"}),
        Index(
            "assets_ticker_ci_prefix_idx",
            func.lower(ticker).label("ticker_lower"),
            postgresql_ops={"ticker_lower": "text_pattern_ops"},
        ),
        Index(
            "assets_name_ci_prefix_idx",
            func.lower(name).label("name_lower"),
            postgresql_ops={"name_lower": "text_pattern_ops"},
        ),
        Index("assets_created_at_idx", text("created_at desc"), "asset_uuid"),
        Index("assets_updated_at_idx", text("updated_at desc"), "asset_uuid"),
        Index(
            "assets_one_active_asset_id_uidx",
            "asset_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "assets_domain_ticker_active_uidx",
            text("lower(domain)"),
            "ticker",
            unique=True,
            postgresql_where=text(
                "status = 'active' and ticker is not null and ticker <> ''"
            ),
        ),
    )


class AssetSerializedFragment(Base, TimestampMixin):
    __tablename__ = "asset_serialized_fragments"

    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.asset_uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    legacy_json: Mapped[str] = mapped_column(Text, nullable=False)
    v2_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("asset_serialized_fragments_asset_idx", "asset_uuid"),)


class AssetMutableMetadata(Base):
    __tablename__ = "asset_mutable_metadata"

    mutable_metadata_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_uuid"), nullable=False, unique=True
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )

    asset: Mapped[Asset] = relationship(back_populates="mutable_metadata")

    __table_args__ = (
        CheckConstraint(
            "schema_version >= 1", name="asset_mutable_metadata_schema_version_chk"
        ),
        Index("asset_mutable_metadata_action_idx", "updated_by_action_uuid"),
    )


class AssetTradingVenue(Base, TimestampMixin):
    __tablename__ = "asset_trading_venues"

    trading_venue_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_uuid"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )

    asset: Mapped[Asset] = relationship(back_populates="trading_venues")

    __table_args__ = (
        UniqueConstraint(
            "asset_uuid", "url", name="asset_trading_venues_asset_url_uidx"
        ),
        UniqueConstraint(
            "asset_uuid", "name", "url", name="asset_trading_venues_asset_name_url_uidx"
        ),
        Index("asset_trading_venues_name_idx", "name", "asset_uuid"),
        Index("asset_trading_venues_asset_position_idx", "asset_uuid", "position"),
    )


class AssetCategoryTag(Base):
    __tablename__ = "asset_category_tags"

    category_tag_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_uuid"), nullable=False
    )
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )

    asset: Mapped[Asset] = relationship(back_populates="category_tags")

    __table_args__ = (
        UniqueConstraint(
            "asset_uuid", "tag", name="asset_category_tags_asset_tag_uidx"
        ),
        Index("asset_category_tags_tag_idx", "tag", "asset_uuid"),
        Index("asset_category_tags_asset_position_idx", "asset_uuid", "position"),
    )


class AssetCustomAttribute(Base, TimestampMixin):
    __tablename__ = "asset_custom_attributes"

    custom_attribute_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_uuid"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=False
    )
    updated_by_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )

    asset: Mapped[Asset] = relationship(back_populates="custom_attributes")

    __table_args__ = (
        UniqueConstraint(
            "asset_uuid", "name", name="asset_custom_attributes_asset_name_uidx"
        ),
        Index("asset_custom_attributes_name_idx", "name", "asset_uuid"),
        Index(
            "asset_custom_attributes_value_gin_idx",
            "value",
            postgresql_using="gin",
            postgresql_ops={"value": "jsonb_path_ops"},
        ),
    )


class AssetAdminAnnotation(Base):
    __tablename__ = "asset_admin_annotations"

    admin_annotation_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_uuid"), nullable=False, unique=True
    )
    asset_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    featured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    malicious: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    delisted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_admin_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by_admin_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    asset: Mapped[Asset] = relationship(back_populates="admin_annotations")

    __table_args__ = (
        Index("asset_admin_asset_type_idx", "asset_type"),
        Index(
            "asset_admin_featured_idx",
            "featured",
            postgresql_where=text("featured = true"),
        ),
        Index(
            "asset_admin_malicious_idx",
            "malicious",
            postgresql_where=text("malicious = true"),
        ),
        Index(
            "asset_admin_delisted_idx",
            "delisted",
            postgresql_where=text("delisted = true"),
        ),
        Index("asset_admin_last_action_idx", "last_admin_action_uuid"),
    )


class IssuerPubkeyHistory(Base):
    __tablename__ = "issuer_pubkey_history"

    issuer_pubkey_history_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_uuid"), nullable=False
    )
    pubkey: Mapped[str] = mapped_column(String(66), nullable=False)
    valid_from_action_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=False
    )
    valid_until_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "pubkey ~ '^(02|03)[0-9a-f]{64}$'",
            name="issuer_pubkey_history_pubkey_format_chk",
        ),
        Index(
            "issuer_pubkey_history_one_current_uidx",
            "asset_uuid",
            unique=True,
            postgresql_where=text("valid_until_action_uuid is null"),
        ),
        Index("issuer_pubkey_history_asset_idx", "asset_uuid", "created_at"),
    )


class Action(Base):
    __tablename__ = "actions"

    action_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    audit_sequence: Mapped[int] = mapped_column(
        BigInteger,
        audit_sequence_global,
        server_default=audit_sequence_global.next_value(),
        unique=True,
        nullable=False,
    )

    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.asset_uuid"), nullable=False
    )
    asset_chain_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonce: Mapped[str | None] = mapped_column(Text, nullable=True)
    issuer_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_pubkey: Mapped[str | None] = mapped_column(String(66), nullable=True)
    admin_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "asset_chain_id ~ '^[0-9a-f]{64}$'",
            name="actions_asset_chain_id_format_chk",
        ),
        CheckConstraint(
            "action_hash is null or action_hash ~ '^[0-9a-f]{64}$'",
            name="actions_action_hash_format_chk",
        ),
        CheckConstraint(
            "verified_pubkey is null or verified_pubkey ~ '^(02|03)[0-9a-f]{64}$'",
            name="actions_verified_pubkey_format_chk",
        ),
        Index(
            "actions_issuer_nonce_uidx",
            "asset_uuid",
            "nonce",
            unique=True,
            postgresql_where=text("actor = 'issuer'"),
        ),
        Index(
            "actions_asset_hash_sequence_idx",
            "asset_uuid",
            "audit_sequence",
            postgresql_where=text("action_hash is not null"),
        ),
        Index("actions_asset_sequence_idx", "asset_uuid", "audit_sequence"),
        Index("actions_chain_asset_sequence_idx", "asset_chain_id", "audit_sequence"),
        Index("actions_operation_sequence_idx", "operation", "audit_sequence"),
        Index("actions_actor_sequence_idx", "actor", "audit_sequence"),
        Index("actions_received_at_idx", "server_received_at", "audit_sequence"),
    )


class AssetIconProposal(Base):
    __tablename__ = "asset_icon_proposals"

    icon_proposal_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    asset_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.asset_uuid", ondelete="CASCADE"),
        nullable=False,
    )
    icon_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=IconProposalStatus.PENDING
    )
    submission_method: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by_action_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=False
    )
    decided_by_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    obsoleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    obsoleted_by_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.action_uuid"), nullable=True
    )

    asset: Mapped[Asset] = relationship(foreign_keys=[asset_uuid])
    proposed_by_action: Mapped[Action] = relationship(
        foreign_keys=[proposed_by_action_uuid]
    )
    decided_by_action: Mapped[Action | None] = relationship(
        foreign_keys=[decided_by_action_uuid]
    )
    obsoleted_by_action: Mapped[Action | None] = relationship(
        foreign_keys=[obsoleted_by_action_uuid]
    )

    __table_args__ = (
        CheckConstraint(
            "icon_hash ~ '^[0-9a-f]{64}$'", name="asset_icon_proposals_hash_format_chk"
        ),
        CheckConstraint(
            "status in ('pending', 'rejected', 'approved')",
            name="asset_icon_proposals_status_chk",
        ),
        CheckConstraint(
            "submission_method in ('v2_issuer_signature', 'admin_upload', 'legacy_import')",
            name="asset_icon_proposals_submission_method_chk",
        ),
        CheckConstraint(
            "status <> 'pending' or image_data is not null",
            name="asset_icon_proposals_pending_data_chk",
        ),
        CheckConstraint(
            "status <> 'rejected' or image_data is null",
            name="asset_icon_proposals_rejected_data_chk",
        ),
        CheckConstraint(
            "image_data is null or octet_length(image_data) <= 1048576",
            name="asset_icon_proposals_data_size_chk",
        ),
        Index(
            "asset_icon_proposals_one_pending_uidx",
            "asset_uuid",
            unique=True,
            postgresql_where=text("status = 'pending' and obsoleted_at is null"),
        ),
        Index(
            "asset_icon_proposals_status_date_idx",
            "status",
            "proposed_at",
            "icon_proposal_uuid",
        ),
        Index("asset_icon_proposals_asset_idx", "asset_uuid", "proposed_at"),
    )


class AdminKey(Base, TimestampMixin):
    __tablename__ = "admin_keys"

    admin_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    pubkey: Mapped[str] = mapped_column(String(66), nullable=False)
    friendly_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_by_admin_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_actions.admin_action_uuid"), nullable=True
    )
    removed_by_admin_action_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_actions.admin_action_uuid"), nullable=True
    )

    permissions: Mapped[list["AdminPermission"]] = relationship(back_populates="admin")
    created_by_action: Mapped["AdminAction | None"] = relationship(
        foreign_keys=[created_by_admin_action_uuid]
    )
    removed_by_action: Mapped["AdminAction | None"] = relationship(
        foreign_keys=[removed_by_admin_action_uuid]
    )

    __table_args__ = (
        CheckConstraint(
            "pubkey ~ '^(02|03)[0-9a-f]{64}$'", name="admin_keys_pubkey_format_chk"
        ),
        CheckConstraint(
            "status in ('active', 'removed')", name="admin_keys_status_chk"
        ),
        UniqueConstraint("pubkey", name="admin_keys_pubkey_uidx"),
        Index("admin_keys_status_idx", "status"),
    )


class AdminPermission(Base):
    __tablename__ = "admin_permissions"

    admin_permission_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    admin_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_keys.admin_uuid"), nullable=False
    )
    permission: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    admin: Mapped[AdminKey] = relationship(back_populates="permissions")

    __table_args__ = (
        UniqueConstraint(
            "admin_uuid", "permission", name="admin_permissions_admin_permission_uidx"
        ),
        Index("admin_permissions_permission_idx", "permission"),
    )


class AdminAction(Base):
    __tablename__ = "admin_actions"

    admin_action_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    audit_sequence: Mapped[int] = mapped_column(
        BigInteger,
        audit_sequence_global,
        server_default=audit_sequence_global.next_value(),
        nullable=False,
    )
    actor_admin_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_keys.admin_uuid"), nullable=False
    )
    actor_pubkey: Mapped[str] = mapped_column(String(66), nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[dict] = mapped_column(JSONB, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    admin_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor_admin: Mapped[AdminKey] = relationship(foreign_keys=[actor_admin_uuid])

    __table_args__ = (
        CheckConstraint(
            "actor_pubkey ~ '^(02|03)[0-9a-f]{64}$'",
            name="admin_actions_actor_pubkey_format_chk",
        ),
        UniqueConstraint("audit_sequence", name="admin_actions_audit_sequence_uidx"),
        Index("admin_actions_sequence_idx", "audit_sequence"),
        Index(
            "admin_actions_actor_nonce_uidx", "actor_admin_uuid", "nonce", unique=True
        ),
        Index("admin_actions_operation_sequence_idx", "operation", "audit_sequence"),
        Index("admin_actions_received_at_idx", "server_received_at", "audit_sequence"),
    )
