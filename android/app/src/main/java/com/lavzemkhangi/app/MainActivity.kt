package com.lavzemkhangi.app

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

private data class ShopProduct(val id: Int, val name: String, val category: String, val symbol: String, val description: String)

class MainActivity : Activity() {
    private val bg = Color.rgb(15, 15, 17)
    private val panel = Color.rgb(27, 27, 30)
    private val panel2 = Color.rgb(36, 35, 37)
    private val gold = Color.rgb(211, 174, 94)
    private val cream = Color.rgb(244, 239, 226)
    private val muted = Color.rgb(166, 161, 151)
    private val products = listOf(
        ShopProduct(1, "یخچال و فریزر", "لوازم آشپزخانه", "❄️", "انواع یخچال و فریزر از برندهای معتبر"),
        ShopProduct(2, "ماشین لباسشویی", "لوازم شست‌وشو", "🫧", "مدل‌های متنوع ماشین لباسشویی"),
        ShopProduct(3, "تلویزیون", "صوتی و تصویری", "📺", "تلویزیون‌های هوشمند و باکیفیت"),
        ShopProduct(4, "اجاق گاز", "لوازم آشپزخانه", "🔥", "اجاق گاز مبله و صفحه‌ای"),
        ShopProduct(5, "جاروبرقی", "نظافت خانه", "🧹", "انواع جاروبرقی و تجهیزات نظافت"),
        ShopProduct(6, "ظرفشویی", "لوازم شست‌وشو", "🍽️", "ماشین ظرفشویی خانگی")
    )
    private val categories = listOf("همه", "لوازم آشپزخانه", "لوازم شست‌وشو", "صوتی و تصویری", "نظافت خانه")
    private val cart = linkedMapOf<Int, Int>()
    private lateinit var content: LinearLayout
    private lateinit var search: EditText
    private var selectedCategory = "همه"
    private var currentTab = "خانه"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = bg
        window.navigationBarColor = bg
        window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS)
        window.decorView.layoutDirection = View.LAYOUT_DIRECTION_RTL
        showHome()
    }

    private fun showHome() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(bg)
            layoutDirection = View.LAYOUT_DIRECTION_RTL
        }
        val scroll = ScrollView(this).apply { isFillViewport = true }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(26))
        }
        scroll.addView(content)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(bottomBar(), LinearLayout.LayoutParams(-1, dp(68)))
        setContentView(root)
        addHeader()
        addHero()
        addSectionTitle("دسته‌بندی‌ها", "مشاهده همه")
        addCategories()
        addSectionTitle("محصولات فروشگاه", "انتخاب و بررسی")
        search = EditText(this).apply {
            hint = "جست‌وجوی محصول..."
            setHintTextColor(muted)
            setTextColor(cream)
            textSize = 14f
            singleLine = true
            setPadding(dp(15), 0, dp(15), 0)
            background = rounded(panel2, 14)
            addTextChangedListener(object : android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { renderProducts() }
                override fun afterTextChanged(s: android.text.Editable?) {}
            })
        }
        content.addView(search, LinearLayout.LayoutParams(-1, dp(48)).apply { bottomMargin = dp(14) })
        val productContainer = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; tag = "products" }
        content.addView(productContainer)
        renderProducts()
        addFooterNote()
    }

    private fun addHeader() {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val brand = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        brand.addView(label("لوازم خانگی", 23f, gold, true))
        brand.addView(label("انتخابی ماندگار برای خانه شما", 11f, muted))
        row.addView(brand, LinearLayout.LayoutParams(0, -2, 1f))
        val cartButton = actionButton("سبد خرید  " + cart.values.sum(), true)
        cartButton.setOnClickListener { showCart() }
        row.addView(cartButton)
        content.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(22) })
    }

    private fun addHero() {
        val hero = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(20), dp(20), dp(20))
            background = GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                intArrayOf(Color.rgb(54, 46, 32), Color.rgb(27, 26, 25), Color.rgb(19, 19, 21))
            ).apply { cornerRadius = dp(20).toFloat(); setStroke(dp(1), Color.rgb(105, 84, 48)) }
        }
        hero.addView(label("تجربه‌ای لوکس برای خانه", 20f, cream, true))
        hero.addView(label("محصولات منتخب خانه را با آرامش بررسی کنید.", 13f, muted).apply {
            setPadding(0, dp(8), 0, dp(14))
        })
        val cta = actionButton("مشاهده محصولات", true)
        cta.setOnClickListener { search.requestFocus() }
        hero.addView(cta, LinearLayout.LayoutParams(-2, dp(42)))
        content.addView(hero, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(25) })
    }

    private fun addSectionTitle(title: String, subtitle: String) {
        val row = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        row.addView(label(title, 17f, cream, true), LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(label(subtitle, 11f, gold))
        content.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(13) })
    }

    private fun addCategories() {
        val horizontal = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        categories.forEach { category ->
            val chip = TextView(this).apply {
                text = category
                textSize = 12f
                setTextColor(if (selectedCategory == category) bg else cream)
                gravity = Gravity.CENTER
                setPadding(dp(14), dp(10), dp(14), dp(10))
                background = rounded(if (selectedCategory == category) gold else panel2, 30)
                setOnClickListener { selectedCategory = category; showHome() }
            }
            horizontal.addView(chip, LinearLayout.LayoutParams(-2, dp(39)).apply { marginEnd = dp(8) })
        }
        val scroller = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            addView(horizontal)
        }
        content.addView(scroller, LinearLayout.LayoutParams(-1, dp(44)).apply { bottomMargin = dp(22) })
    }

    private fun renderProducts() {
        val container = content.findViewWithTag<LinearLayout>("products") ?: return
        container.removeAllViews()
        val term = if (::search.isInitialized) search.text.toString().trim() else ""
        val filtered = products.filter {
            (selectedCategory == "همه" || it.category == selectedCategory) &&
                (term.isBlank() || it.name.contains(term, true) || it.category.contains(term, true))
        }
        if (filtered.isEmpty()) container.addView(label("محصولی با این جست‌وجو پیدا نشد.", 13f, muted))
        filtered.forEach { product ->
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(14), dp(13), dp(14), dp(13))
                background = rounded(panel, 17)
            }
            val art = TextView(this).apply {
                text = product.symbol
                textSize = 29f
                gravity = Gravity.CENTER
                background = rounded(panel2, 14)
            }
            card.addView(art, LinearLayout.LayoutParams(dp(64), dp(64)).apply { marginEnd = dp(13) })
            val details = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            details.addView(label(product.name, 15f, cream, true))
            details.addView(label(product.category, 11f, muted).apply { setPadding(0, dp(4), 0, dp(8)) })
            details.addView(label("برای اطلاع از قیمت و موجودی، جزئیات را ببینید", 10f, gold))
            card.addView(details, LinearLayout.LayoutParams(0, -2, 1f))
            val viewButton = TextView(this).apply {
                text = "جزئیات"
                textSize = 11f
                setTextColor(gold)
                setPadding(dp(10), dp(9), dp(10), dp(9))
                background = rounded(Color.rgb(61, 51, 34), 10)
                setOnClickListener { showProduct(product) }
            }
            card.addView(viewButton)
            container.addView(card, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(11) })
        }
    }

    private fun showProduct(product: ShopProduct) {
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(18), dp(22), dp(8))
            setBackgroundColor(panel)
        }
        body.addView(label(product.symbol, 42f, gold))
        body.addView(label(product.name, 20f, cream, true).apply { setPadding(0, dp(10), 0, dp(8)) })
        body.addView(label(product.description, 14f, muted))
        body.addView(label("قیمت و موجودی نهایی پس از اتصال به اطلاعات زنده فروشگاه نمایش داده می‌شود.", 12f, gold)
            .apply { setPadding(0, dp(14), 0, dp(8)) })
        val dialog = AlertDialog.Builder(this)
            .setView(body)
            .setNegativeButton("بستن", null)
            .setPositiveButton("افزودن به سبد") { _, _ ->
                cart[product.id] = (cart[product.id] ?: 0) + 1
                Toast.makeText(this, "به سبد خرید اضافه شد", Toast.LENGTH_SHORT).show()
                showHome()
            }.show()
        dialog.window?.setBackgroundDrawable(rounded(panel, 18))
    }

    private fun showCart() {
        val lines = cart.mapNotNull { entry ->
            val product = products.find { it.id == entry.key }
            if (product == null) null else "• " + product.name + "  ×  " + entry.value
        }
        val message = if (lines.isEmpty()) "سبد خرید شما خالی است." else
            lines.joinToString("\n") + "\n\nثبت سفارش نهایی پس از اتصال امن به سرور فروشگاه فعال می‌شود."
        AlertDialog.Builder(this)
            .setTitle("سبد خرید")
            .setMessage(message)
            .setNegativeButton("ادامه خرید", null)
            .setPositiveButton(if (cart.isEmpty()) "باشه" else "ثبت سفارش") { _, _ ->
                if (cart.isNotEmpty()) Toast.makeText(this, "ثبت سفارش پس از اتصال API فروشگاه فعال می‌شود.", Toast.LENGTH_LONG).show()
            }.show()
    }

    private fun bottomBar(): View {
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.rgb(23, 23, 25))
            setPadding(dp(8), dp(7), dp(8), dp(7))
        }
        listOf("خانه" to "⌂", "دسته‌ها" to "▦", "سبد خرید" to "🛒", "حساب من" to "♙").forEach { pair ->
            val name = pair.first
            val icon = pair.second
            val item = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                setOnClickListener {
                    when (name) {
                        "سبد خرید" -> showCart()
                        "خانه" -> { currentTab = name; showHome() }
                        else -> Toast.makeText(this@MainActivity, "این بخش در مرحله بعد تکمیل می‌شود.", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            item.addView(label(icon, 20f, if (currentTab == name) gold else muted))
            item.addView(label(name, 10f, if (currentTab == name) gold else muted))
            bar.addView(item, LinearLayout.LayoutParams(0, -1, 1f))
        }
        return bar
    }

    private fun addFooterNote() {
        content.addView(label("نسخه اولیه رابط بومی اندروید • اتصال اطلاعات فروشگاه در مرحله بعد", 10f, muted)
            .apply { gravity = Gravity.CENTER; setPadding(0, dp(12), 0, dp(4)) })
    }

    private fun actionButton(text: String, primary: Boolean): Button = Button(this).apply {
        this.text = text
        textSize = 12f
        isAllCaps = false
        setTextColor(if (primary) bg else gold)
        background = rounded(if (primary) gold else panel2, 12)
        minHeight = 0
        minimumHeight = 0
        setPadding(dp(14), 0, dp(14), 0)
    }

    private fun label(text: String, size: Float, color: Int, bold: Boolean = false) =
        TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(color)
            typeface = Typeface.create("sans-serif", if (bold) Typeface.BOLD else Typeface.NORMAL)
            gravity = Gravity.CENTER_VERTICAL
        }

    private fun rounded(color: Int, radius: Int) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = dp(radius).toFloat()
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()
}
