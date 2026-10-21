
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import PushSubscription, User
from app.core.deps import get_current_user

router = APIRouter()


class PushSubscriptionRequest(BaseModel):
    endpoint: HttpUrl
    keys: dict[str, str]


@router.post("/push/subscribe")
def subscribe_to_push(
    payload: PushSubscriptionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p256dh = payload.keys.get("p256dh")
    auth = payload.keys.get("auth")

    if not p256dh or not auth:
        raise HTTPException(
            status_code=422,
            detail="Missing push encryption keys",
        )

    endpoint = str(payload.endpoint)

    subscription = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == endpoint)
        .first()
    )

    if subscription:
        subscription.user_id = current_user.id
        subscription.p256dh = p256dh
        subscription.auth = auth
    else:
        subscription = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        db.add(subscription)

    db.commit()

    return {"message": "Push subscription saved successfully"}