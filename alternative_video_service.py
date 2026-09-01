"""Transactional management of canonical Alternative YouTube videos."""

from activity import record_activity, resolve_collaborator
from youtube_media import normalize_youtube_url, parse_youtube_url, youtube_embed_url


class AlternativeVideoError(ValueError): pass
class VideoAlreadyCurrent(AlternativeVideoError): pass
class CurrentVideoExists(AlternativeVideoError): pass
class NoCurrentVideo(AlternativeVideoError): pass


def _video_row(row):
    if row is None: return None
    result = dict(row); result["video_id"] = parse_youtube_url(result["storage_key"])
    result["watch_url"] = normalize_youtube_url(result["storage_key"])
    result["embed_url"] = youtube_embed_url(result["video_id"])
    return result


def get_current_video(connection, alternative_id):
    return _video_row(connection.execute("""SELECT am.*,m.storage_key,m.mime_type FROM alternative_media am JOIN media_asset m USING(media_asset_id) WHERE am.alternative_id=? AND am.role='catalog_video' AND am.is_current=1""",(alternative_id,)).fetchone())


def get_video_history(connection, alternative_id):
    return [_video_row(row) for row in connection.execute("""SELECT am.*,m.storage_key,m.mime_type,n.media_asset_id AS replaced_by_media_asset_id,nm.storage_key AS replaced_by_url FROM alternative_media am JOIN media_asset m USING(media_asset_id) LEFT JOIN alternative_media n ON n.supersedes_alternative_media_id=am.alternative_media_id LEFT JOIN media_asset nm ON nm.media_asset_id=n.media_asset_id WHERE am.alternative_id=? AND am.role='catalog_video' ORDER BY am.created_at DESC,am.alternative_media_id DESC""",(alternative_id,))]


def _asset(connection, url):
    row=connection.execute("SELECT media_asset_id,mime_type,origin_kind FROM media_asset WHERE storage_key=?",(url,)).fetchone()
    if row:
        if row["mime_type"]!="video/youtube" or row["origin_kind"]!="external_reference": raise AlternativeVideoError("La URL ya está registrada como un recurso incompatible.")
        return row["media_asset_id"]
    return connection.execute("INSERT INTO media_asset(storage_backend,storage_key,mime_type,origin_kind,origin_label) VALUES('external',?,'video/youtube','external_reference','YouTube')",(url,)).lastrowid


def _actor(connection, actor):
    role=actor.get("access_role"); collaborator_id,name=resolve_collaborator(connection,actor.get("collaborator_id"))
    if role not in ("reviewer","master"): raise AlternativeVideoError("El rol no puede gestionar videos.")
    return role,collaborator_id,name


def _alternative_exists(connection, alternative_id):
    if connection.execute("SELECT 1 FROM alternative WHERE alternative_id=?",(alternative_id,)).fetchone() is None: raise AlternativeVideoError("La alternativa no existe.")


def add_video(connection, alternative_id, url, actor):
    normalized=normalize_youtube_url(url); role,cid,name=_actor(connection,actor)
    try:
        connection.execute("BEGIN IMMEDIATE"); _alternative_exists(connection,alternative_id)
        if get_current_video(connection,alternative_id): raise CurrentVideoExists("La alternativa ya tiene un video vigente.")
        asset_id=_asset(connection,normalized)
        link_id=connection.execute("""INSERT INTO alternative_media(alternative_id,media_asset_id,role,is_current,created_by_collaborator_id,created_by_name_snapshot,created_access_role) VALUES(?,?,'catalog_video',1,?,?,?)""",(alternative_id,asset_id,cid,name,role)).lastrowid
        record_activity(connection,"alternative_video_added",entity_type="alternative",entity_id=alternative_id,collaborator_id=cid,access_role=role,comment=normalized)
        connection.commit(); return link_id
    except Exception: connection.rollback(); raise


def replace_video(connection, alternative_id, url, actor):
    normalized=normalize_youtube_url(url); role,cid,name=_actor(connection,actor)
    try:
        connection.execute("BEGIN IMMEDIATE"); current=get_current_video(connection,alternative_id)
        if not current: raise NoCurrentVideo("La alternativa no tiene un video vigente.")
        if current["video_id"]==parse_youtube_url(normalized): raise VideoAlreadyCurrent("El video indicado ya es el video vigente de esta alternativa.")
        connection.execute("UPDATE alternative_media SET is_current=0,retired_at=CURRENT_TIMESTAMP WHERE alternative_media_id=?",(current["alternative_media_id"],))
        asset_id=_asset(connection,normalized)
        link_id=connection.execute("""INSERT INTO alternative_media(alternative_id,media_asset_id,role,is_current,supersedes_alternative_media_id,created_by_collaborator_id,created_by_name_snapshot,created_access_role) VALUES(?,?,'catalog_video',1,?,?,?,?)""",(alternative_id,asset_id,current["alternative_media_id"],cid,name,role)).lastrowid
        record_activity(connection,"alternative_video_replaced",entity_type="alternative",entity_id=alternative_id,collaborator_id=cid,access_role=role,comment=normalized)
        connection.commit(); return link_id
    except Exception: connection.rollback(); raise


def retire_video(connection, alternative_id, actor, comment=None):
    role,cid,_=_actor(connection,actor)
    try:
        connection.execute("BEGIN IMMEDIATE"); current=get_current_video(connection,alternative_id)
        if not current: raise NoCurrentVideo("La alternativa no tiene un video vigente.")
        connection.execute("UPDATE alternative_media SET is_current=0,retired_at=CURRENT_TIMESTAMP WHERE alternative_media_id=?",(current["alternative_media_id"],))
        record_activity(connection,"alternative_video_retired",entity_type="alternative",entity_id=alternative_id,collaborator_id=cid,access_role=role,comment=comment)
        connection.commit()
    except Exception: connection.rollback(); raise
