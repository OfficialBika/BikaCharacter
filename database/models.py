"""Document shape notes.

Motor is schema-less, so the app writes MongoDB dictionaries directly. This file is
kept as readable documentation of the main collections.

photos:
  cardId, name, normalizedName, rarity, anime, fileId, addedBy, createdAt, updatedAt

users:
  userId, username, firstName, lastName, exp, favoriteCardId, haremView, cards[]

groups:
  groupId, title, username, isApproved, approvedBy, approvedAt, messageCount,
  changeTime, activeDrop, lastSpeakerId, lastSpeakerCount, createdAt, updatedAt

transfers:
  fromUserId, toUserId, cardId, name, rarity, anime, qty, createdAt

bot_mutes:
  groupId, userId, mutedUntil, reason, createdAt

bot_settings:
  _id=config, adderIds[], updatedAt

harem_transfers:
  fromUserId, toUserId, cardUniqueCount, cardTotalCount, exp, byOwnerId, createdAt

claim_logs:
  userId, username, firstName, lastName, groupId, groupTitle, groupUsername,
  cardId, name, rarity, anime, yangonDate, createdAt

daily_claim_limits:
  userId, date, count, createdAt, updatedAt
"""
