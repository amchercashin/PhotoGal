export const CONTENT_CATEGORIES = [
  { key: 'portrait',     label: 'Портреты' },
  { key: 'selfie',       label: 'Селфи' },
  { key: 'group_photo',  label: 'Групповые' },
  { key: 'nature',       label: 'Природа' },
  { key: 'architecture', label: 'Архитектура' },
  { key: 'monument',     label: 'Памятники' },
  { key: 'museum',       label: 'Музеи' },
  { key: 'food',         label: 'Еда' },
  { key: 'animals',      label: 'Животные' },
  { key: 'transport',    label: 'Транспорт' },
  { key: 'interior',     label: 'Интерьеры' },
  { key: 'sports',       label: 'Спорт' },
  { key: 'event',        label: 'Мероприятия' },
  { key: 'book',         label: 'Книги' },
] as const

export const TECHNICAL_CATEGORIES = [
  { key: 'screenshot',   label: 'Скриншоты' },
  { key: 'receipt',      label: 'Чеки' },
  { key: 'document',     label: 'Документы' },
  { key: 'carsharing',   label: 'Каршеринг' },
  { key: 'meme',         label: 'Мемы' },
  { key: 'screen_photo', label: 'Фото экрана' },
  { key: 'qr_code',      label: 'QR/штрихкоды' },
  { key: 'reference',    label: 'Справочные' },
] as const

export const ALL_CATEGORIES = [...CONTENT_CATEGORIES, ...TECHNICAL_CATEGORIES]
